"""YouTube provider — yt-dlp powered."""

from __future__ import annotations

import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.domain.entities.media_info import (
    DownloadOption,
    DownloadResult,
    MediaInfo,
)
from app.domain.enums import MediaKind, Platform
from app.domain.text_utils import truncate_description
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
        raw = await self._ytdlp.extract_info(url, extra_opts=self._auth_extra_opts())
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
        # Per-bucket real size. yt-dlp already knows the true ``filesize``
        # / ``filesize_approx`` of every format it offers; reading those
        # is dramatically more accurate than the constant-bitrate formula
        # ``_estimate_video_size`` used before (off by 3-6x on low-motion
        # H.264 clips). JSON stores the map with string keys, so we
        # normalize at write time to keep ``build_options`` lookups
        # independent of cache round-trips. Fallback to the formula stays
        # for sources that don't publish filesize (rare on YouTube but
        # observed on private / age-gated items).
        size_by_height = {
            str(h): s
            for h in _VIDEO_HEIGHTS
            if (s := _real_video_size_for_height(formats, h)) is not None
        }

        info = MediaInfo(
            platform=Platform.YOUTUBE,
            media_id=str(entry.get("id") or ""),
            title=str(entry.get("title") or "video"),
            kind=MediaKind.VIDEO,
            duration_sec=float(entry["duration"]) if entry.get("duration") else None,
            thumbnail_url=entry.get("thumbnail"),
            description=truncate_description(
                entry.get("description"),
                max_chars=self._settings.POST_TEXT_MAX_CHARS,
            ),
            raw={
                "source_url": url,
                "webpage_url": entry.get("webpage_url"),
                "available_heights": heights,
                "duration": entry.get("duration"),
                "size_by_height": size_by_height,
            },
        )
        return info

    def build_options(self, info: MediaInfo) -> list[DownloadOption]:
        heights: list[int] = list(info.raw.get("available_heights") or [])
        max_h = max(heights) if heights else 0
        duration = info.raw.get("duration")
        size_by_height = info.raw.get("size_by_height") or {}

        options: list[DownloadOption] = []
        for h in _VIDEO_HEIGHTS:
            # Show a bucket if either an exact match exists or the source has a higher
            # resolution that we can downscale-pick from.
            if h <= max_h or h in heights:
                real_size = size_by_height.get(str(h))
                estimated = (
                    int(real_size)
                    if isinstance(real_size, int | float) and real_size > 0
                    else _estimate_video_size(h, duration)
                )
                options.append(
                    DownloadOption(
                        key=f"video_{h}",
                        label=f"Видео {h}p",
                        kind=MediaKind.VIDEO,
                        height=h,
                        container="mp4",
                        estimated_size_bytes=estimated,
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

    def default_option(self, info: MediaInfo) -> DownloadOption:
        # Instant-download flow skips the picker UI, so we pick the
        # highest video bucket the source supports. Audio-only is a
        # minority case on YouTube and users can still request it via
        # the classic picker path (INSTANT_DOWNLOAD_ENABLED=false).
        # ADR-0010 §2.1: raise DownloadError on genuinely unsupported
        # payloads rather than silently returning audio — a follow-up
        # upstream fix should surface, not be masked.
        options = self.build_options(info)
        video_options = [
            o
            for o in options
            if o.kind is MediaKind.VIDEO and o.height is not None and o.key.startswith("video_")
        ]
        if not video_options:
            raise DownloadError(
                f"No downloadable video heights for {info.media_id or info.title!r}"
            )
        # build_options emits buckets in ascending height order; pick the tallest.
        return max(video_options, key=lambda o: o.height or 0)

    async def probe_size(
        self,
        url: str,
        *,
        info: MediaInfo,
        option: DownloadOption,
    ) -> int | None:
        del info
        return await self._ytdlp.probe_size(
            url,
            format_spec=_format_spec_for_option(option),
            extra_opts=self._auth_extra_opts(),
        )

    async def download(
        self,
        url: str,
        option: DownloadOption,
        *,
        target_dir: str,
        on_progress: Callable[[float], None] | None = None,
    ) -> DownloadResult:
        out_dir = self._target_path(target_dir)

        if option.kind is MediaKind.AUDIO:
            files = await self._ytdlp.download(
                url,
                format_spec=_format_spec_for_option(option),
                target_dir=out_dir,
                postprocessors=[
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": str(option.bitrate_kbps or 192),
                    }
                ],
                extra_opts=self._auth_extra_opts(),
                on_progress=on_progress,
            )
            files = [p for p in files if p.suffix.lower() == ".mp3"] or files
            return self._build_result(files, info_title=out_dir.name, kind=MediaKind.AUDIO)

        if option.kind is MediaKind.VIDEO and option.height is not None:
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
            files = await self._ytdlp.download(
                url,
                format_spec=_format_spec_for_option(option),
                target_dir=out_dir,
                merge_output_format="mp4",
                extra_opts=self._auth_extra_opts(),
                on_progress=on_progress,
            )
            return self._build_result(files, info_title=out_dir.name, kind=MediaKind.VIDEO)

        raise DownloadError(f"Unsupported YouTube option: {option.key}")

    def _auth_extra_opts(self) -> dict[str, str] | None:
        cookiefile = (self._settings.YOUTUBE_COOKIES_FILE or "").strip()
        if not cookiefile:
            return None
        cookie_path = Path(cookiefile)
        if not cookie_path.is_file():
            _logger.warning("youtube_cookiefile_missing", path=cookiefile)
            return None
        return {"cookiefile": str(cookie_path)}

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
    # Only used as a *fallback* when yt-dlp does not publish a concrete
    # filesize for the eligible format — see ``_real_video_size_for_height``
    # for the happy-path lookup that drives the button labels today.
    bitrate_kbps = {360: 800, 480: 1200, 720: 2500, 1080: 5000}.get(height, 2500)
    return int(bitrate_kbps * 1000 / 8 * duration_sec)


def _real_video_size_for_height(
    formats: list[dict[str, Any]],
    target_height: int,
) -> int | None:
    """Pick the ``video + audio`` filesize the download selector will
    actually land on for a given bucket.

    Mirrors the full cascade from ``YouTubeProvider.download``:

    1. ``bestvideo[height<=h][vcodec^=avc1][ext=mp4]``
    2. ``bestvideo[height<=h][vcodec^=avc1]``
    3. ``bestvideo[height<=h][ext=mp4]``
    4. ``bestvideo[height<=h]``

    Each chain is tried in order; we stop at the first one that has at
    least one format with a concrete ``filesize`` / ``filesize_approx``.
    This matters because on many YouTube videos 1080p only exists as
    VP9/AV1 (no H.264 above 720p), so chain 1 is empty and a naive
    estimator would fall back to the bitrate formula — off by 3-6x.
    Broadening the match lets us report a real size for chain 3/4 too
    (the VP9 filesize is close enough to the post-download size we
    actually deliver, since the later ``_ensure_mobile_compatible``
    transcode preserves the original video bitrate on -c copy paths).

    Audio: best ``m4a``; fallback to any audio-only track.
    """

    def _size(f: dict[str, Any]) -> int:
        raw = f.get("filesize") or f.get("filesize_approx") or 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    def _is_video(f: dict[str, Any]) -> bool:
        return (
            isinstance(f.get("height"), int)
            and f["height"] <= target_height
            and f.get("vcodec") not in (None, "none")
            and _size(f) > 0
        )

    chains: tuple[Callable[[dict[str, Any]], bool], ...] = (
        lambda f: str(f.get("vcodec") or "").startswith("avc1") and f.get("ext") == "mp4",
        lambda f: str(f.get("vcodec") or "").startswith("avc1"),
        lambda f: f.get("ext") == "mp4",
        lambda _f: True,
    )
    best_video: dict[str, Any] | None = None
    for predicate in chains:
        eligible = [f for f in formats if _is_video(f) and predicate(f)]
        if eligible:
            # yt-dlp's ``bestvideo`` ranks by height, then bitrate --
            # approximate the latter with filesize (which scales with
            # bitrate x duration for a fixed clip).
            best_video = max(eligible, key=lambda f: (int(f["height"]), _size(f)))
            break
    if best_video is None:
        return None
    video_size = _size(best_video)

    # Audio cascade mirrors ``+bestaudio[ext=m4a] / +bestaudio``.
    audio_candidates_m4a = [
        f
        for f in formats
        if f.get("vcodec") in (None, "none") and f.get("ext") == "m4a" and _size(f) > 0
    ]
    audio_candidates_any = [
        f for f in formats if f.get("vcodec") in (None, "none") and _size(f) > 0
    ]
    audio_size = max(
        (_size(f) for f in (audio_candidates_m4a or audio_candidates_any)),
        default=0,
    )
    return video_size + audio_size


def _estimate_audio_size(bitrate_kbps: int, duration_sec: float | None) -> int | None:
    if not duration_sec:
        return None
    return int(bitrate_kbps * 1000 / 8 * duration_sec)


def _format_spec_for_option(option: DownloadOption) -> str:
    if option.kind is MediaKind.AUDIO:
        return "bestaudio/best"
    if option.kind is MediaKind.VIDEO and option.height is not None:
        h = option.height
        return (
            f"bestvideo[height<={h}][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]"
            f"/bestvideo[height<={h}][vcodec^=avc1]+bestaudio[ext=m4a]"
            f"/bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]"
            f"/bestvideo[height<={h}]+bestaudio"
            f"/best[height<={h}]"
        )
    raise DownloadError(f"Unsupported YouTube option for format probe: {option.key}")
