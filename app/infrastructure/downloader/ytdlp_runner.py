"""
Async wrapper around yt-dlp's ``YoutubeDL`` Python API.

The library is synchronous, so we run heavy operations inside
``asyncio.to_thread`` to avoid blocking the event loop. Errors are translated
into the project's exception hierarchy so the upper layers can map them to
user messages.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from app.config import Settings
from app.exceptions import (
    DownloadError,
    MediaNotFoundError,
    MediaPrivateError,
    ProviderError,
)
from app.logging_config import get_logger

_logger = get_logger(__name__)


class YtDlpRunner:
    """Thin async facade over yt-dlp."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def extract_info(self, url: str) -> dict[str, Any]:
        """Fetch metadata only; no download."""
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": False,
            "extract_flat": False,
            "socket_timeout": 30,
            "retries": 1,
        }
        try:
            return await asyncio.to_thread(self._extract_sync, url, opts)
        except YtDlpDownloadError as exc:
            raise self._classify(exc) from exc
        except Exception as exc:
            _logger.exception("ytdlp_extract_unexpected", url=url)
            raise ProviderError(f"yt-dlp failure: {exc}") from exc

    async def download(
        self,
        url: str,
        *,
        format_spec: str,
        target_dir: Path,
        postprocessors: list[dict[str, Any]] | None = None,
        merge_output_format: str | None = None,
        extra_opts: dict[str, Any] | None = None,
    ) -> list[Path]:
        """Download the media. Returns list of resulting file paths in target_dir."""
        target_dir.mkdir(parents=True, exist_ok=True)

        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "format": format_spec,
            "outtmpl": str(target_dir / "%(title).80B [%(id)s].%(ext)s"),
            "restrictfilenames": True,
            "concurrent_fragment_downloads": 4,
            "socket_timeout": 30,
            "retries": 2,
            "fragment_retries": 2,
            "noplaylist": False,
            "ffmpeg_location": self._settings.FFMPEG_BIN,
        }
        if merge_output_format:
            opts["merge_output_format"] = merge_output_format
        if postprocessors:
            opts["postprocessors"] = postprocessors
        if extra_opts:
            opts.update(extra_opts)

        try:
            await asyncio.to_thread(self._download_sync, url, opts)
        except YtDlpDownloadError as exc:
            raise self._classify(exc) from exc
        except Exception as exc:
            _logger.exception("ytdlp_download_unexpected", url=url)
            raise DownloadError(f"yt-dlp download failed: {exc}") from exc

        return sorted(p for p in target_dir.iterdir() if p.is_file())

    @staticmethod
    def _extract_sync(url: str, opts: dict[str, Any]) -> dict[str, Any]:
        with YoutubeDL(opts) as ydl:
            return ydl.sanitize_info(ydl.extract_info(url, download=False))

    @staticmethod
    def _download_sync(url: str, opts: dict[str, Any]) -> None:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])

    @staticmethod
    def _classify(exc: YtDlpDownloadError) -> ProviderError | DownloadError:
        msg = str(exc).lower()
        if (
            "private" in msg
            or "login required" in msg
            or ("requested format is not available" in msg and "private" in msg)
        ):
            return MediaPrivateError(str(exc))
        if "not found" in msg or "does not exist" in msg or "removed" in msg or "404" in msg:
            return MediaNotFoundError(str(exc))
        if "unsupported url" in msg:
            return ProviderError(str(exc))
        return DownloadError(str(exc))
