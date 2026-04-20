"""YouTube provider — yt-dlp powered."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from app.domain.entities.media_info import (
    DownloadOption,
    DownloadResult,
    MediaInfo,
)
from app.domain.enums import MediaKind, Platform
from app.exceptions import DownloadError
from app.infrastructure.providers.base import BaseProvider
from app.logging_config import get_logger

_logger = get_logger(__name__)

# Quality buckets we expose to the user. Real availability is intersected with
# what the source actually provides.
_VIDEO_HEIGHTS = (360, 480, 720, 1080)


class YouTubeProvider(BaseProvider):
    platform = Platform.YOUTUBE

    async def get_info(self, url: str) -> MediaInfo:
        raw = await self._ytdlp.extract_info(url)
        # `entries` would mean a playlist; we treat the first entry as the target.
        entry: dict[str, Any] = raw["entries"][0] if raw.get("entries") else raw

        formats: list[dict[str, Any]] = entry.get("formats") or []
        heights = sorted(
            {
                int(f["height"])
                for f in formats
                if f.get("vcodec") not in (None, "none") and f.get("height")
            }
        )

        info = MediaInfo(
            platform=Platform.YOUTUBE,
            media_id=str(entry.get("id") or ""),
            title=str(entry.get("title") or "video"),
            kind=MediaKind.VIDEO,
            duration_sec=float(entry["duration"]) if entry.get("duration") else None,
            thumbnail_url=entry.get("thumbnail"),
            raw={
                "source_url": url,
                "webpage_url": entry.get("webpage_url"),
                "available_heights": heights,
                "duration": entry.get("duration"),
            },
        )
        return info

    def build_options(self, info: MediaInfo) -> list[DownloadOption]:
        heights: list[int] = list(info.raw.get("available_heights") or [])
        max_h = max(heights) if heights else 0
        duration = info.raw.get("duration")

        options: list[DownloadOption] = []
        for h in _VIDEO_HEIGHTS:
            # Show a bucket if either an exact match exists or the source has a higher
            # resolution that we can downscale-pick from.
            if h <= max_h or h in heights:
                options.append(
                    DownloadOption(
                        key=f"video_{h}",
                        label=f"Видео {h}p",
                        kind=MediaKind.VIDEO,
                        height=h,
                        container="mp4",
                        estimated_size_bytes=_estimate_video_size(h, duration),
                    )
                )

        # Audio MP3 is always offered if there is any audio at all.
        options.append(
            DownloadOption(
                key="audio_mp3",
                label="Только аудио (MP3)",
                kind=MediaKind.AUDIO,
                bitrate_kbps=192,
                container="mp3",
                estimated_size_bytes=_estimate_audio_size(192, duration),
            )
        )
        return options

    async def download(
        self,
        url: str,
        option: DownloadOption,
        *,
        target_dir: str,
    ) -> DownloadResult:
        out_dir = self._target_path(target_dir)

        if option.kind is MediaKind.AUDIO:
            files = await self._ytdlp.download(
                url,
                format_spec="bestaudio/best",
                target_dir=out_dir,
                postprocessors=[
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": str(option.bitrate_kbps or 192),
                    }
                ],
            )
            files = [p for p in files if p.suffix.lower() == ".mp3"] or files
            return self._build_result(files, info_title=out_dir.name, kind=MediaKind.AUDIO)

        if option.kind is MediaKind.VIDEO and option.height is not None:
            h = option.height
            # Prefer H.264 (avc1) + AAC because Telegram's mobile clients
            # decode through hardware codecs that only handle that pair
            # reliably. VP9/AV1 (common on YouTube 1080p60) would merge
            # into an MP4 that plays on desktop but freezes on phones.
            # The ``_ensure_mobile_compatible`` post-step transcodes as
            # a last resort, but winning the selector is much cheaper
            # than re-encoding after download, so we try avc1 first and
            # only fall back when YouTube truly has no H.264 at that
            # height (rare: H.264 tops out at 1080p30 for newer videos,
            # so 1080p might land on vp9 and require transcoding).
            fmt = (
                f"bestvideo[height<={h}][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]"
                f"/bestvideo[height<={h}][vcodec^=avc1]+bestaudio[ext=m4a]"
                f"/bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
                f"/bestvideo[height<={h}]+bestaudio"
                f"/best[height<={h}]"
            )
            files = await self._ytdlp.download(
                url,
                format_spec=fmt,
                target_dir=out_dir,
                merge_output_format="mp4",
            )
            return self._build_result(files, info_title=out_dir.name, kind=MediaKind.VIDEO)

        raise DownloadError(f"Unsupported YouTube option: {option.key}")

    def _build_result(
        self,
        files: list[Path],
        *,
        info_title: str,
        kind: MediaKind,
    ) -> DownloadResult:
        if not files:
            raise DownloadError("yt-dlp produced no files")
        total = sum(f.stat().st_size for f in files if f.exists())
        primary = files[0]
        mime = mimetypes.guess_type(primary.name)[0] or "application/octet-stream"
        _logger.info(
            "youtube_download_done",
            files=len(files),
            total_bytes=total,
            primary=primary.name,
            mime=mime,
        )
        return DownloadResult(
            files=tuple(str(f) for f in files),
            total_size_bytes=total,
            primary_mime=mime,
            title=info_title,
            kind=kind,
        )


def _estimate_video_size(height: int, duration_sec: float | None) -> int | None:
    if not duration_sec:
        return None
    # Rough bitrate buckets (kbps) for MP4/H.264 at common YT qualities.
    bitrate_kbps = {360: 800, 480: 1200, 720: 2500, 1080: 5000}.get(height, 2500)
    return int(bitrate_kbps * 1000 / 8 * duration_sec)


def _estimate_audio_size(bitrate_kbps: int, duration_sec: float | None) -> int | None:
    if not duration_sec:
        return None
    return int(bitrate_kbps * 1000 / 8 * duration_sec)
