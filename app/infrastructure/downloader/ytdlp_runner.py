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

Option builders live in ``ytdlp_opts``, outcome interpretation in
``ytdlp_results`` and the ffmpeg post-step in ``mobile_compat``.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from app.config import Settings
from app.exceptions import (
    AppError,
    DownloadError,
    DownloadTimeoutError,
    ProviderError,
    UpstreamUnavailableError,
)
from app.infrastructure.cache.redis_circuit_breaker import RedisCircuitBreaker
from app.infrastructure.downloader import ytdlp_opts
from app.infrastructure.downloader.mobile_compat import make_mobile_compatible
from app.infrastructure.downloader.ytdlp_opts import YtDlpOpts, host_of, make_progress_hook
from app.infrastructure.downloader.ytdlp_results import (
    classify_error,
    extract_known_size,
    looks_like_throttle,
)
from app.logging_config import get_logger

_logger = get_logger(__name__)


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

    async def extract_info(
        self,
        url: str,
        *,
        extra_opts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch metadata only; no download."""
        opts = self._finalize(ytdlp_opts.extract_opts(), extra_opts)
        return await self._guarded(
            url,
            lambda: self._extract_sync(url, opts),
            unexpected_event="ytdlp_extract_unexpected",
            wrap_unexpected=lambda exc: ProviderError(f"yt-dlp failure: {exc}"),
        )

    async def probe_size(
        self,
        url: str,
        *,
        format_spec: str,
        extra_opts: dict[str, Any] | None = None,
    ) -> int | None:
        """Best-effort size probe without downloading bytes."""
        opts = self._finalize(ytdlp_opts.probe_opts(format_spec), extra_opts)
        result = await self._guarded(
            url,
            lambda: self._extract_sync(url, opts),
            unexpected_event="ytdlp_probe_unexpected",
            wrap_unexpected=lambda exc: DownloadError(f"yt-dlp size probe failed: {exc}"),
        )
        return extract_known_size(result)

    async def download(
        self,
        url: str,
        *,
        format_spec: str,
        target_dir: Path,
        postprocessors: list[dict[str, Any]] | None = None,
        merge_output_format: str | None = None,
        extra_opts: dict[str, Any] | None = None,
        force_transcode: bool = False,
        on_progress: Callable[[float], None] | None = None,
    ) -> list[Path]:
        """Download the media. Returns list of resulting file paths in target_dir.

        ``on_progress`` is invoked from yt-dlp's ``progress_hooks`` with
        a float percent in [0.0, 100.0]. The callback runs inside the
        ``asyncio.to_thread`` worker, NOT in the caller's event loop,
        so it must be purely synchronous and thread-safe. Errors raised
        by the callback are swallowed (yt-dlp has no recovery path for
        hook failures and we do not want progress reporting to abort a
        download). See ADR-0010 §2.2.
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        base = ytdlp_opts.download_opts(format_spec, target_dir)
        if on_progress is not None:
            base["progress_hooks"] = [make_progress_hook(on_progress)]
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
            base["ffmpeg_location"] = resolved_ffmpeg
        else:
            _logger.warning("ffmpeg_binary_not_resolved", configured=self._settings.FFMPEG_BIN)
        if merge_output_format:
            base["merge_output_format"] = merge_output_format
        if postprocessors:
            base["postprocessors"] = postprocessors
        opts = self._finalize(base, extra_opts)

        # Audit fix A10: bound the yt-dlp call by
        # ``DOWNLOAD_TIMEOUT_SECONDS`` independently of arq's
        # ``JOB_TIMEOUT_SECONDS``. Without this, a hung/slow source
        # consumed the entire job budget (download + transcode + upload)
        # and arq prematurely killed the worker mid-ffmpeg.
        #
        # Known limitation: ``asyncio.wait_for`` on ``to_thread`` cannot
        # cancel the in-flight thread — the background yt-dlp work keeps
        # running until its socket timeout (``socket_timeout=30``) trips.
        # The caller has already returned ``DownloadError("timeout")``,
        # so the job is correctly marked failed; the thread terminates
        # shortly after and its output files are GC'd by the cleanup
        # sweep. A clean kill requires moving yt-dlp to a subprocess
        # (tracked as a follow-up — see audit-fixes phase 3 backlog).
        download_timeout = self._settings.DOWNLOAD_TIMEOUT_SECONDS
        try:
            await self._guarded(
                url,
                lambda: self._download_sync(url, opts),
                unexpected_event="ytdlp_download_unexpected",
                wrap_unexpected=lambda exc: DownloadError(f"yt-dlp download failed: {exc}"),
                timeout_s=download_timeout,
            )
        except TimeoutError as exc:
            _logger.warning("ytdlp_download_timeout", url=url, timeout_s=download_timeout)
            raise DownloadTimeoutError(f"yt-dlp timed out after {download_timeout}s") from exc

        files = sorted(p for p in target_dir.iterdir() if p.is_file())
        if resolved_ffmpeg:
            resolved_ffprobe = shutil.which(self._settings.FFPROBE_BIN)
            await make_mobile_compatible(
                files, resolved_ffmpeg, resolved_ffprobe, force_transcode=force_transcode
            )
        return files

    def _finalize(self, opts: YtDlpOpts, extra_opts: dict[str, Any] | None) -> YtDlpOpts:
        """Source guards → provider overrides → proxy, in that order."""
        ytdlp_opts.apply_source_guards(opts)
        if extra_opts:
            opts.update(extra_opts)
        ytdlp_opts.apply_proxy(opts, self._settings.HTTPS_PROXY_URL)
        return opts

    async def _guarded[T](
        self,
        url: str,
        call: Callable[[], T],
        *,
        unexpected_event: str,
        wrap_unexpected: Callable[[Exception], AppError],
        timeout_s: float | None = None,
    ) -> T:
        """Run a blocking yt-dlp call with circuit breaker and error mapping.

        ``TimeoutError`` (only possible with ``timeout_s``) propagates
        unchanged so the caller can map it to its own timeout error.
        """
        host = host_of(url)
        await self._trip_if_open(host)
        try:
            pending = asyncio.to_thread(call)
            if timeout_s is None:
                result = await pending
            else:
                result = await asyncio.wait_for(pending, timeout=timeout_s)
        except TimeoutError:
            raise
        except YtDlpDownloadError as exc:
            await self._report_if_throttle(host, exc)
            raise self._classify(exc) from exc
        except Exception as exc:
            _logger.exception(unexpected_event, url=url)
            raise wrap_unexpected(exc) from exc
        await self._note_success(host)
        return result

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
        if not looks_like_throttle(exc):
            return
        await self._breaker.record_failure(host)

    @staticmethod
    def _extract_sync(url: str, opts: YtDlpOpts) -> dict[str, Any]:
        with YoutubeDL(opts) as ydl:
            return ydl.sanitize_info(ydl.extract_info(url, download=False))

    @staticmethod
    def _download_sync(url: str, opts: YtDlpOpts) -> None:
        with YoutubeDL(opts) as ydl:
            ydl.download([url])

    @staticmethod
    def _classify(exc: YtDlpDownloadError) -> ProviderError | DownloadError:
        return classify_error(exc)
