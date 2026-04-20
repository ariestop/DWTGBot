"""
Async wrapper around yt-dlp's ``YoutubeDL`` Python API.

The library is synchronous, so we run heavy operations inside
``asyncio.to_thread`` to avoid blocking the event loop. Errors are translated
into the project's exception hierarchy so the upper layers can map them to
user messages.

L5 (audit fix): upstream throttling is short-circuited through a
per-host :class:`RedisCircuitBreaker`. N consecutive throttle-shaped
failures within ``CB_WINDOW_SECONDS`` open the breaker for
``CB_COOLDOWN_SECONDS``; subsequent calls fast-fail with
:class:`UpstreamUnavailableError` (retryable). This protects the IP
from being banned by the upstream platform when every worker keeps
retrying a 429.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from app.config import Settings
from app.exceptions import (
    DownloadError,
    MediaNotFoundError,
    MediaPrivateError,
    ProviderError,
    UpstreamUnavailableError,
)
from app.infrastructure.cache.redis_circuit_breaker import RedisCircuitBreaker
from app.logging_config import get_logger

_logger = get_logger(__name__)


# Keywords inside ``YtDlpDownloadError`` messages that signal an
# upstream throttle worth reporting to the circuit breaker. Keep this
# narrow: classifying generic 5xx / network errors as throttles would
# open the breaker on unrelated blips.
_THROTTLE_MARKERS = (
    "http error 429",
    "too many requests",
    "rate limit",
    "rate-limit",
    "rate limited",
)


class YtDlpRunner:
    """Thin async facade over yt-dlp."""

    def __init__(
        self,
        settings: Settings,
        *,
        breaker: RedisCircuitBreaker | None = None,
    ) -> None:
        self._settings = settings
        self._breaker = breaker

    async def extract_info(self, url: str) -> dict[str, Any]:
        """Fetch metadata only; no download."""
        host = _host_of(url)
        await self._trip_if_open(host)
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": False,
            "extract_flat": False,
            "socket_timeout": 30,
            "retries": 1,
        }
        self._apply_proxy(opts)
        try:
            result = await asyncio.to_thread(self._extract_sync, url, opts)
        except YtDlpDownloadError as exc:
            await self._report_if_throttle(host, exc)
            raise self._classify(exc) from exc
        except Exception as exc:
            _logger.exception("ytdlp_extract_unexpected", url=url)
            raise ProviderError(f"yt-dlp failure: {exc}") from exc
        await self._note_success(host)
        return result

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
        host = _host_of(url)
        await self._trip_if_open(host)
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
        }
        # yt-dlp treats ``ffmpeg_location`` as an absolute path to the binary
        # or a directory containing it and verifies via ``os.path.exists``. A
        # bare name like ``"ffmpeg"`` therefore resolves to False and the
        # postprocessor bails out with "ffmpeg is not installed" even when
        # it is perfectly reachable through ``PATH``. Resolve the configured
        # value explicitly; if resolution fails, omit the option altogether
        # and let yt-dlp's own auto-discovery (which also searches ``PATH``)
        # handle it.
        resolved_ffmpeg = shutil.which(self._settings.FFMPEG_BIN)
        if resolved_ffmpeg:
            opts["ffmpeg_location"] = resolved_ffmpeg
        else:
            _logger.warning(
                "ffmpeg_binary_not_resolved",
                configured=self._settings.FFMPEG_BIN,
            )
        if merge_output_format:
            opts["merge_output_format"] = merge_output_format
        if postprocessors:
            opts["postprocessors"] = postprocessors
        if extra_opts:
            opts.update(extra_opts)
        self._apply_proxy(opts)

        try:
            await asyncio.to_thread(self._download_sync, url, opts)
        except YtDlpDownloadError as exc:
            await self._report_if_throttle(host, exc)
            raise self._classify(exc) from exc
        except Exception as exc:
            _logger.exception("ytdlp_download_unexpected", url=url)
            raise DownloadError(f"yt-dlp download failed: {exc}") from exc

        await self._note_success(host)
        return sorted(p for p in target_dir.iterdir() if p.is_file())

    async def _trip_if_open(self, host: str) -> None:
        if self._breaker is None or not host:
            return
        if await self._breaker.is_open(host):
            _logger.warning("circuit_breaker_call_rejected", host=host)
            raise UpstreamUnavailableError(
                f"breaker open for {host}",
            )

    async def _note_success(self, host: str) -> None:
        if self._breaker is None or not host:
            return
        await self._breaker.record_success(host)

    async def _report_if_throttle(self, host: str, exc: YtDlpDownloadError) -> None:
        if self._breaker is None or not host:
            return
        if not _looks_like_throttle(exc):
            return
        await self._breaker.record_failure(host)

    def _apply_proxy(self, opts: dict[str, Any]) -> None:
        """S9: inject ``HTTPS_PROXY_URL`` into yt-dlp opts when configured.

        yt-dlp accepts both ``http(s)://`` and ``socks5://`` schemes via
        the same ``proxy`` key. We never override an explicit proxy
        already passed in ``extra_opts`` (giving providers an escape
        hatch when they need a per-request override).
        """
        if "proxy" in opts:
            return
        proxy_url = (self._settings.HTTPS_PROXY_URL or "").strip()
        if proxy_url:
            opts["proxy"] = proxy_url

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


def _host_of(url: str) -> str:
    """Normalise the host used as the circuit-breaker key.

    Strip ``www.`` so that ``www.youtube.com`` and ``youtube.com``
    share the breaker — same upstream policy applies. An empty
    return disables the breaker for that call (callers must handle
    ``""`` as "no tracking").
    """
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _looks_like_throttle(exc: YtDlpDownloadError) -> bool:
    """Does this error look like upstream is rate-limiting us?

    Kept narrow on purpose — see the module docstring rationale.
    """
    msg = str(exc).lower()
    return any(marker in msg for marker in _THROTTLE_MARKERS)
