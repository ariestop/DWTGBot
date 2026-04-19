"""
Decide how to deliver a downloaded result back to the user.

Rules:
  - Single file <= telegram_max_upload_bytes  → upload to Telegram (video/audio/photo/document).
  - Multiple files (gallery) all <= limit & total <= 10 items → send file-by-file.
  - Otherwise → package as ZIP (if multiple) and serve via temp link.

The service is stateless and side-effect agnostic w.r.t. domain — it only
calls the sender / temp link / storage objects it was given.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.application.services.temp_link_service import TempLinkService
from app.config import Settings
from app.domain.entities.media_info import DownloadResult
from app.domain.enums import DeliveryMethod, MediaKind
from app.exceptions import FileTooLargeError
from app.infrastructure.storage.local_storage import LocalStorage
from app.infrastructure.telegram.sender import TelegramSender
from app.logging_config import get_logger

_logger = get_logger(__name__)

# We avoid send_media_group complexity for the baseline; per-file uploads
# are predictable and easy to debug. Cap how many we send individually before
# preferring a zipped temp link.
MAX_INDIVIDUAL_FILES = 10


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    method: DeliveryMethod
    public_url: str | None
    primary_telegram_file_id: str | None
    delivered_path: str
    file_size: int


class DeliveryService:
    def __init__(
        self,
        *,
        settings: Settings,
        sender: TelegramSender,
        storage: LocalStorage,
        temp_links: TempLinkService,
    ) -> None:
        self._settings = settings
        self._sender = sender
        self._storage = storage
        self._temp_links = temp_links

    async def deliver(
        self,
        *,
        job_id: int,
        chat_id: int,
        result: DownloadResult,
    ) -> DeliveryOutcome:
        files = [Path(p) for p in result.files]
        self._storage.assert_under_max(result.total_size_bytes)

        max_tg = self._settings.telegram_max_upload_bytes

        # Single file path
        if len(files) == 1:
            f = files[0]
            size = f.stat().st_size
            if size <= max_tg:
                file_id = await self._upload_one(
                    chat_id, f, kind=result.kind, caption=_caption(result)
                )
                return DeliveryOutcome(
                    method=DeliveryMethod.TELEGRAM_UPLOAD,
                    public_url=None,
                    primary_telegram_file_id=file_id,
                    delivered_path=str(f),
                    file_size=size,
                )
            return await self._deliver_via_link(job_id=job_id, chat_id=chat_id, file=f)

        # Gallery path
        small_each = all(f.stat().st_size <= max_tg for f in files)
        if small_each and len(files) <= MAX_INDIVIDUAL_FILES:
            primary_file_id: str | None = None
            for f in files:
                fid = await self._upload_one(chat_id, f, kind=_infer_kind(f), caption=None)
                primary_file_id = primary_file_id or fid
            await self._sender.send_text(chat_id, _caption(result))
            return DeliveryOutcome(
                method=DeliveryMethod.TELEGRAM_UPLOAD,
                public_url=None,
                primary_telegram_file_id=primary_file_id,
                delivered_path=str(files[0]),
                file_size=result.total_size_bytes,
            )

        # Too big or too many → zip + temp link
        zip_path = self._storage.package_zip(files, job_id=job_id, base_name=result.title)
        return await self._deliver_via_link(job_id=job_id, chat_id=chat_id, file=zip_path)

    async def _deliver_via_link(self, *, job_id: int, chat_id: int, file: Path) -> DeliveryOutcome:
        size = file.stat().st_size
        if size > self._settings.max_file_size_bytes:
            raise FileTooLargeError(f"File {file.name} exceeds MAX_FILE_SIZE_MB")

        _, url = await self._temp_links.issue(job_id=job_id, file_path=str(file))
        await self._sender.send_text(
            chat_id,
            f"📦 Файл слишком большой для Telegram.\n"
            f'Скачать (TTL ограничен): <a href="{url}">{file.name}</a>',
        )
        _logger.info("delivered_via_temp_link", job_id=job_id, size=size, file=file.name)
        return DeliveryOutcome(
            method=DeliveryMethod.TEMP_LINK,
            public_url=url,
            primary_telegram_file_id=None,
            delivered_path=str(file),
            file_size=size,
        )

    async def _upload_one(
        self, chat_id: int, file: Path, *, kind: MediaKind, caption: str | None
    ) -> str | None:
        if kind is MediaKind.VIDEO:
            return await self._sender.send_video(chat_id, file, caption)
        if kind is MediaKind.AUDIO:
            return await self._sender.send_audio(chat_id, file, caption)
        if kind is MediaKind.PHOTO:
            return await self._sender.send_photo(chat_id, file, caption)
        return await self._sender.send_document(chat_id, file, caption)


def _caption(result: DownloadResult) -> str:
    return f"<b>{_escape(result.title)}</b>"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
_AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac"}


def _infer_kind(path: Path) -> MediaKind:
    ext = path.suffix.lower()
    if ext in _VIDEO_EXTS:
        return MediaKind.VIDEO
    if ext in _IMAGE_EXTS:
        return MediaKind.PHOTO
    if ext in _AUDIO_EXTS:
        return MediaKind.AUDIO
    return MediaKind.GALLERY
