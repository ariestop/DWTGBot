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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from app.application.services.post_text_store import PostTextStore
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

# Label on the inline button that reveals the source post's
# description. Kept in sync with ADR-0010 §2.3; changing the label
# does not require a callback-data migration (data is still ``pt|N``)
# but translators should edit this constant, not the callback encoder.
_POST_TEXT_BUTTON_LABEL = "Получить текст поста"
# Invitation line appended to the media caption / summary text when a
# post-text button is going to be shown. Without this hint the button
# alone read as orphaned UI — users missed that tapping it reveals the
# original caption. We keep it as a separate constant so translators
# can edit wording without touching delivery plumbing.
_POST_TEXT_HINT = "Нажмите, чтобы получить текст поста 👇"

# Wire format ``pt|<job_id>``. Inlined here to avoid an
# application → bot import (see `.cursor/rules/20-architecture-layers`).
# Source of truth for decoding: ``app.bot.callbacks.codec.PostTextCallback``.
_POST_TEXT_CALLBACK_PREFIX = "pt"


def _post_text_callback_data(job_id: int) -> str:
    return f"{_POST_TEXT_CALLBACK_PREFIX}|{job_id}"


def _with_post_text_hint(caption: str, *, post_text_markup: object | None) -> str:
    """Append the "tap the button" hint to ``caption`` when a button
    will actually be attached to this message. Callers already have the
    markup in hand, so threading it through avoids a second EXISTS
    round-trip against Redis."""
    if post_text_markup is None:
        return caption
    return f"{caption}\n\n{_POST_TEXT_HINT}"


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
        post_text_store: PostTextStore | None = None,
    ) -> None:
        self._settings = settings
        self._sender = sender
        self._storage = storage
        self._temp_links = temp_links
        # Optional so legacy call-sites (tests, ad-hoc scripts) still
        # construct ``DeliveryService`` without wiring the store. In
        # production ``composition.build_worker`` always provides it.
        self._post_text = post_text_store

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

        # Build the post-text button once per delivery. EXISTS is cheap
        # (<1ms on LAN Redis) and the double-check here guards against
        # the narrow window where the TTL expires between auto-enqueue
        # and delivery — rendering a button that immediately alerts
        # "больше недоступен" would look broken.
        post_text_markup = await self._post_text_markup(job_id=job_id)

        # Single file path
        if len(files) == 1:
            f = files[0]
            size = f.stat().st_size
            if size <= max_tg:
                try:
                    file_id = await self._upload_one(
                        chat_id,
                        f,
                        kind=result.kind,
                        caption=_with_post_text_hint(
                            _caption(result, size, footer=self._settings.BRAND_FOOTER),
                            post_text_markup=post_text_markup,
                        ),
                        reply_markup=post_text_markup,
                    )
                except TelegramError as exc:
                    # Fall back to a temp link when Telegram rejects the
                    # direct upload. Most common causes: transient
                    # ``BadRequest`` on the video endpoint (Telegram
                    # tightens validation without warning — unusual
                    # colour primaries, odd aspect ratio, ffmpeg-style
                    # fragmented MP4 boxes), or a network blip late in
                    # the multipart POST. Previously these escaped as
                    # non-``AppError`` exceptions, arq retried, and the
                    # last attempt surfaced the generic fallback text
                    # (see ``mark_terminally_failed``) -- even though
                    # the file was already on disk and perfectly
                    # deliverable via the temp-link path that we use
                    # unconditionally for >50 MB files.
                    _logger.warning(
                        "telegram_upload_fallback_to_link",
                        job_id=job_id,
                        size=size,
                        file=f.name,
                        error_class=type(exc).__name__,
                        error=str(exc),
                    )
                    return await self._deliver_via_link(
                        job_id=job_id,
                        chat_id=chat_id,
                        file=f,
                        reply_markup=post_text_markup,
                    )
                return DeliveryOutcome(
                    method=DeliveryMethod.TELEGRAM_UPLOAD,
                    public_url=None,
                    primary_telegram_file_id=file_id,
                    delivered_path=str(f),
                    file_size=size,
                )
            return await self._deliver_via_link(
                job_id=job_id,
                chat_id=chat_id,
                file=f,
                reply_markup=post_text_markup,
            )

        # Gallery path
        small_each = all(f.stat().st_size <= max_tg for f in files)
        if small_each and len(files) <= MAX_INDIVIDUAL_FILES:
            primary_file_id: str | None = None
            for f in files:
                fid = await self._upload_one(chat_id, f, kind=_infer_kind(f), caption=None)
                primary_file_id = primary_file_id or fid
            await self._sender.send_text(
                chat_id,
                _with_post_text_hint(
                    _caption(result, result.total_size_bytes, footer=self._settings.BRAND_FOOTER),
                    post_text_markup=post_text_markup,
                ),
                reply_markup=post_text_markup,
            )
            return DeliveryOutcome(
                method=DeliveryMethod.TELEGRAM_UPLOAD,
                public_url=None,
                primary_telegram_file_id=primary_file_id,
                delivered_path=str(files[0]),
                file_size=result.total_size_bytes,
            )

        # Too big or too many → zip + temp link
        zip_path = self._storage.package_zip(files, job_id=job_id, base_name=result.title)
        return await self._deliver_via_link(
            job_id=job_id,
            chat_id=chat_id,
            file=zip_path,
            reply_markup=post_text_markup,
        )

    async def _deliver_via_link(
        self,
        *,
        job_id: int,
        chat_id: int,
        file: Path,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> DeliveryOutcome:
        size = file.stat().st_size
        if size > self._settings.max_file_size_bytes:
            raise FileTooLargeError(f"File {file.name} exceeds MAX_FILE_SIZE_MB")

        _, url = await self._temp_links.issue(job_id=job_id, file_path=str(file))
        message = (
            f"📦 Файл слишком большой для Telegram ({_format_size(size)}).\n"
            f'Скачать (TTL ограничен): <a href="{url}">{file.name}</a>'
        )
        footer = self._settings.BRAND_FOOTER
        if footer:
            message = f"{message}\n\n{footer}"
        message = _with_post_text_hint(message, post_text_markup=reply_markup)
        await self._sender.send_text(chat_id, message, reply_markup=reply_markup)
        _logger.info("delivered_via_temp_link", job_id=job_id, size=size, file=file.name)
        return DeliveryOutcome(
            method=DeliveryMethod.TEMP_LINK,
            public_url=url,
            primary_telegram_file_id=None,
            delivered_path=str(file),
            file_size=size,
        )

    async def _upload_one(
        self,
        chat_id: int,
        file: Path,
        *,
        kind: MediaKind,
        caption: str | None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> str | None:
        if kind is MediaKind.VIDEO:
            return await self._sender.send_video(chat_id, file, caption, reply_markup=reply_markup)
        if kind is MediaKind.AUDIO:
            return await self._sender.send_audio(chat_id, file, caption, reply_markup=reply_markup)
        if kind is MediaKind.PHOTO:
            return await self._sender.send_photo(chat_id, file, caption, reply_markup=reply_markup)
        return await self._sender.send_document(chat_id, file, caption, reply_markup=reply_markup)

    async def _post_text_markup(self, *, job_id: int) -> InlineKeyboardMarkup | None:
        """Return the "Получить текст поста" keyboard iff the key
        exists. Any failure (no store wired, Redis outage) maps to
        ``None`` so delivery proceeds without a button — losing the
        button is a graceful degradation, losing the video would not
        be."""
        if self._post_text is None:
            return None
        try:
            present = await self._post_text.exists(job_id=job_id)
        except Exception:  # pragma: no cover  defensive
            _logger.exception("delivery_post_text_exists_failed", job_id=job_id)
            return None
        if not present:
            return None
        button = InlineKeyboardButton(
            _POST_TEXT_BUTTON_LABEL,
            callback_data=_post_text_callback_data(job_id),
        )
        return InlineKeyboardMarkup([[button]])


def _caption(result: DownloadResult, size_bytes: int, *, footer: str = "") -> str:
    base = f"<b>{_escape(result.title)}</b>\nРазмер файла: {_format_size(size_bytes)}"
    if not footer:
        return base
    return f"{base}\n\n{_escape(footer)}"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_size(n: int) -> str:
    """Human-readable size for captions (binary units, 1 decimal place)."""
    if n < 1024:
        return f"{n} B"
    unit_pairs = (("KB", 1024), ("MB", 1024**2), ("GB", 1024**3))
    for name, factor in reversed(unit_pairs):
        if n >= factor:
            return f"{n / factor:.1f} {name}"
    return f"{n} B"


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
