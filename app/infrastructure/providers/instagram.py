"""
Instagram provider — yt-dlp powered.

yt-dlp returns a single dict for posts/reels and a multi-entry result for
carousels/galleries. We fold those into a uniform ``MediaInfo`` and offer a
small, sensible set of options:

  - single video:    "Скачать видео"
  - single photo:    "Скачать фото"
  - gallery (mixed): "Скачать всё" (zip-packed) and per-kind shortcuts
                     when the gallery contains video AND photo items.

Galleries are downloaded into a per-job directory, then either sent
file-by-file (handled in delivery service) or packaged as one ZIP if there
are several files — that decision is made by the delivery service based on
size and Telegram limits.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from app.domain.entities.media_info import (
    DownloadOption,
    DownloadResult,
    MediaInfo,
    MediaItem,
)
from app.domain.enums import MediaKind, Platform
from app.domain.text_utils import truncate_description
from app.exceptions import DownloadError
from app.infrastructure.providers.base import BaseProvider
from app.logging_config import get_logger

_logger = get_logger(__name__)


class InstagramProvider(BaseProvider):
    platform = Platform.INSTAGRAM

    async def get_info(self, url: str) -> MediaInfo:
        raw = await self._ytdlp.extract_info(url)

        entries = raw.get("entries")
        if entries:
            items, kinds = _items_from_entries(entries)
            kind = MediaKind.GALLERY if len(items) > 1 else (kinds[0] if kinds else MediaKind.PHOTO)
        else:
            items = (_item_from_entry(raw),)
            kind = items[0].kind

        title = str(raw.get("title") or raw.get("description") or "Instagram media")[:200]
        # IG captions live under ``description`` in the yt-dlp sanitized
        # dict. ``title`` above may alias to the same value when title is
        # missing (legacy shortcuts), so description is still useful as
        # the full-length caption for the "Получить текст поста" flow.
        description = truncate_description(
            raw.get("description"),
            max_chars=self._settings.POST_TEXT_MAX_CHARS,
        )

        return MediaInfo(
            platform=Platform.INSTAGRAM,
            media_id=str(raw.get("id") or ""),
            title=title,
            kind=kind,
            duration_sec=float(raw["duration"]) if raw.get("duration") else None,
            items=items,
            thumbnail_url=raw.get("thumbnail"),
            description=description,
            raw={
                "source_url": url,
                "webpage_url": raw.get("webpage_url"),
                "is_gallery": bool(entries),
                "kinds": [k.value for k in (kinds if entries else [items[0].kind])],
            },
        )

    def build_options(self, info: MediaInfo) -> list[DownloadOption]:
        options: list[DownloadOption] = []

        if info.kind is MediaKind.GALLERY:
            kinds = set(info.raw.get("kinds") or [])
            options.append(
                DownloadOption(
                    key="gallery_all",
                    label=f"Скачать всё ({len(info.items)})",
                    kind=MediaKind.GALLERY,
                )
            )
            if MediaKind.VIDEO.value in kinds and MediaKind.PHOTO.value in kinds:
                options.append(
                    DownloadOption(key="gallery_videos", label="Только видео", kind=MediaKind.VIDEO)
                )
                options.append(
                    DownloadOption(key="gallery_photos", label="Только фото", kind=MediaKind.PHOTO)
                )
            return options

        if info.kind is MediaKind.VIDEO:
            options.append(
                DownloadOption(key="single_video", label="Скачать видео", kind=MediaKind.VIDEO)
            )
        elif info.kind is MediaKind.PHOTO:
            options.append(
                DownloadOption(key="single_photo", label="Скачать фото", kind=MediaKind.PHOTO)
            )
        return options

    def default_option(self, info: MediaInfo) -> DownloadOption:
        # Instagram has at most one "shape" per post, so the default is
        # the only option we build: gallery_all for carousels, single_*
        # for standalone reels/photos. ADR-0010 §2.1 forbids silent
        # fallback — if build_options yields nothing we raise instead.
        options = self.build_options(info)
        if not options:
            raise DownloadError(
                f"Instagram post has no downloadable shape (kind={info.kind.value})"
            )
        if info.kind is MediaKind.GALLERY:
            for opt in options:
                if opt.key == "gallery_all":
                    return opt
            # Fallthrough guards against a future build_options refactor
            # that drops gallery_all — explicit error beats a subtly
            # wrong default for mixed carousels.
            raise DownloadError("Instagram gallery default (gallery_all) missing from options")
        return options[0]

    async def download(
        self,
        url: str,
        option: DownloadOption,
        *,
        target_dir: str,
    ) -> DownloadResult:
        out_dir = self._target_path(target_dir)

        playlist_items = self._compute_playlist_items(option, url=url)
        # Instagram's MP4s routinely pass the ffprobe "looks H.264/AAC"
        # check yet still freeze on mobile Telegram -- typical culprits
        # are fragmented/segmented mp4 (pulled from a DASH manifest),
        # High@5.x profile, or unusual GOP spacing. A forced transcode
        # flattens the output to a known-good H.264 Main@4.0 mp4 that
        # mobile hardware decoders handle reliably. The slow path is
        # only invoked for the video items inside the batch.
        files = await self._ytdlp.download(
            url,
            format_spec="bestvideo*+bestaudio/best",
            target_dir=out_dir,
            merge_output_format="mp4",
            extra_opts=({"playlist_items": playlist_items} if playlist_items else None),
            force_transcode=True,
        )

        if option.kind is MediaKind.PHOTO:
            files = [f for f in files if _looks_like_image(f)]
        elif option.kind is MediaKind.VIDEO and option.key.startswith("gallery_"):
            files = [f for f in files if _looks_like_video(f)]

        if not files:
            raise DownloadError("Instagram download produced no matching files")

        total = sum(f.stat().st_size for f in files if f.exists())
        primary = files[0]
        mime = mimetypes.guess_type(primary.name)[0] or "application/octet-stream"
        kind_out = (
            MediaKind.GALLERY if option.kind is MediaKind.GALLERY or len(files) > 1 else option.kind
        )
        _logger.info(
            "instagram_download_done",
            files=len(files),
            total_bytes=total,
            primary=primary.name,
            kind=kind_out.value,
        )
        return DownloadResult(
            files=tuple(str(f) for f in files),
            total_size_bytes=total,
            primary_mime=mime,
            title=out_dir.name,
            kind=kind_out,
        )

    def _compute_playlist_items(self, option: DownloadOption, *, url: str) -> str | None:
        # We currently rely on yt-dlp to expand the gallery; filtering happens by
        # MIME after download. This is robust to yt-dlp's per-item indexing changes.
        # Hook is kept for future per-index selection from inline UI.
        del option, url
        return None


# -------------------------- helpers --------------------------


def _item_from_entry(entry: dict[str, Any]) -> MediaItem:
    kind = (
        MediaKind.VIDEO
        if entry.get("vcodec") not in (None, "none") or entry.get("ext") in {"mp4", "mov", "webm"}
        else MediaKind.PHOTO
    )
    return MediaItem(
        kind=kind,
        url=str(entry.get("url") or entry.get("webpage_url") or ""),
        width=entry.get("width"),
        height=entry.get("height"),
        duration_sec=float(entry["duration"]) if entry.get("duration") else None,
    )


def _items_from_entries(
    entries: list[dict[str, Any]],
) -> tuple[tuple[MediaItem, ...], list[MediaKind]]:
    items: list[MediaItem] = []
    kinds: list[MediaKind] = []
    for entry in entries:
        item = _item_from_entry(entry)
        items.append(item)
        kinds.append(item.kind)
    return tuple(items), kinds


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def _looks_like_image(path: Path) -> bool:
    return path.suffix.lower() in _IMAGE_EXTS


def _looks_like_video(path: Path) -> bool:
    return path.suffix.lower() in _VIDEO_EXTS
