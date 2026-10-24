"""
Async wrapper around yt-dlp's ``YoutubeDL`` Python API.

The library is synchronous. Metadata calls (``extract_info``,
``probe_size``) run inside ``asyncio.to_thread``; ``download`` runs in a
child process (``ytdlp_child``, audit R16) so a timeout or a cancelled
job can kill it, ffmpeg included. Errors are translated into the
project's exception hierarchy so the upper layers can map them to user
messages.

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
import contextlib
import json
import os
import shutil
import signal
import sys
from collections.abc import Awaitable, Callable
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
from app.infrastructure.downloader.http_probe import head_content_length
from app.infrastructure.downloader.mobile_compat import make_mobile_compatible
from app.infrastructure.downloader.ytdlp_opts import YtDlpOpts, host_of
from app.infrastructure.downloader.ytdlp_results import (
    classify_error,
    extract_known_size,
    looks_like_throttle,
    selected_media_urls,
)
from app.logging_config import get_logger

_logger = get_logger(__name__)

CHILD_ARGV = (sys.executable, "-m", "app.infrastructure.downloader.ytdlp_child")
# The directory holding the ``app`` package: ``-m`` resolves from here.
_APP_ROOT = Path(__file__).resolve().parents[3]


class _ChildFailedError(RuntimeError):
    """The download process failed outside yt-dlp or died without an answer."""


class YtDlpRunner:
    """Thin async facade over yt-dlp."""

    def __init__(
        self,
        settings: Settings,
        *,
        breaker: RedisCircuitBreaker | None = None,
        child_argv: tuple[str, ...] = CHILD_ARGV,
    ) -> None:
        self._settings = settings
        self._breaker = breaker
        self._child_argv = child_argv

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
            lambda: asyncio.to_thread(self._extract_sync, url, opts),
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
            lambda: asyncio.to_thread(self._extract_sync, url, opts),
            unexpected_event="ytdlp_probe_unexpected",
            wrap_unexpected=lambda exc: DownloadError(f"yt-dlp size probe failed: {exc}"),
        )
        known = extract_known_size(result)
        if known is not None:
            return known
        return await self._head_probe_size(result)

    async def _head_probe_size(self, result: dict[str, Any]) -> int | None:
        """Sum ``Content-Length`` of the selected format URLs; any miss → ``None``."""
        urls = selected_media_urls(result)
        if not urls:
            return None
        proxy_url = (self._settings.HTTPS_PROXY_URL or "").strip() or None
        lengths = await asyncio.gather(
            *(asyncio.to_thread(head_content_length, u, proxy_url=proxy_url) for u in urls)
        )
        if any(length is None for length in lengths):
            return None
        total = sum(length for length in lengths if length is not None)
        _logger.info("ytdlp_size_from_head", urls=len(urls), size_bytes=total)
        return total

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

        ``on_progress`` gets a float percent in [0.0, 100.0] as the
        child process reports it. The callback runs in a worker thread
        (``asyncio.to_thread``), NOT in the caller's event loop, so it
        must be purely synchronous and thread-safe. Errors raised by the
        callback are logged and swallowed: progress reporting must never
        abort a download. See ADR-0010 §2.2.
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        base = ytdlp_opts.download_opts(format_spec, target_dir)
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
        # The child adds the source guards: ``match_filter`` is a function.
        opts = self._finalize(base, extra_opts, guards=False)

        # Audit fix A10: bound the yt-dlp call by
        # ``DOWNLOAD_TIMEOUT_SECONDS`` independently of arq's
        # ``JOB_TIMEOUT_SECONDS``. Without this, a hung/slow source
        # consumed the entire job budget (download + transcode + upload)
        # and arq prematurely killed the worker mid-ffmpeg. On timeout,
        # and when arq cancels the job, ``_run_child`` kills the child's
        # process group (audit R16).
        download_timeout = self._settings.DOWNLOAD_TIMEOUT_SECONDS
        try:
            await self._guarded(
                url,
                lambda: self._run_child(url, opts, on_progress),
                unexpected_event="ytdlp_download_unexpected",
                wrap_unexpected=lambda exc: DownloadError(f"yt-dlp download failed: {exc}"),
                timeout_s=download_timeout,
            )
        except TimeoutError as exc:
            _logger.warning("ytdlp_download_timeout", host=host_of(url), timeout_s=download_timeout)
            raise DownloadTimeoutError(f"yt-dlp timed out after {download_timeout}s") from exc

        files = sorted(p for p in target_dir.iterdir() if p.is_file())
        if resolved_ffmpeg:
            resolved_ffprobe = shutil.which(self._settings.FFPROBE_BIN)
            await make_mobile_compatible(
                files, resolved_ffmpeg, resolved_ffprobe, force_transcode=force_transcode
            )
        return files

    def _finalize(
        self, opts: YtDlpOpts, extra_opts: dict[str, Any] | None, *, guards: bool = True
    ) -> YtDlpOpts:
        """Source guards → provider overrides → proxy, in that order."""
        if guards:
            ytdlp_opts.apply_source_guards(opts)
        if extra_opts:
            opts.update(extra_opts)
        ytdlp_opts.apply_proxy(opts, self._settings.HTTPS_PROXY_URL)
        return opts

    async def _run_child(
        self,
        url: str,
        opts: YtDlpOpts,
        on_progress: Callable[[float], None] | None,
    ) -> None:
        """Run one download in ``ytdlp_child``; kill its group if we stop early.

        Raises ``YtDlpDownloadError`` with the child's message so
        :meth:`_guarded` classifies it as before.
        """
        request = json.dumps({"url": url, "opts": opts}).encode()
        proc = await asyncio.create_subprocess_exec(
            *self._child_argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd=_APP_ROOT,
            start_new_session=True,
            # yt-dlp error messages can be long; the default is 64 KiB.
            limit=1 << 20,
        )
        outcome: dict[str, Any] = {}
        try:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(request)
            await proc.stdin.drain()
            proc.stdin.close()
            async for line in proc.stdout:
                message = _parse_line(line)
                if "progress" in message:
                    if on_progress is not None:
                        await _report_progress(on_progress, message["progress"])
                elif message:
                    outcome = message
            await proc.wait()
        finally:
            if proc.returncode is None:
                # Timeout or cancelled job: yt-dlp and its ffmpeg go too.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
        if outcome.get("ok") is True:
            return
        if outcome.get("error") == "ytdlp":
            raise YtDlpDownloadError(str(outcome.get("message", "")))
        detail = outcome.get("message") or f"exit code {proc.returncode}, no result"
        raise _ChildFailedError(str(detail))

    async def _guarded[T](
        self,
        url: str,
        call: Callable[[], Awaitable[T]],
        *,
        unexpected_event: str,
        wrap_unexpected: Callable[[Exception], AppError],
        timeout_s: float | None = None,
    ) -> T:
        """Await a yt-dlp call with circuit breaker and error mapping.

        ``TimeoutError`` (only possible with ``timeout_s``) propagates
        unchanged so the caller can map it to its own timeout error.
        """
        host = host_of(url)
        await self._trip_if_open(host)
        try:
            pending = call()
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
            _logger.exception(unexpected_event, host=host)
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
    def _classify(exc: YtDlpDownloadError) -> ProviderError | DownloadError:
        return classify_error(exc)


def _parse_line(line: bytes) -> dict[str, Any]:
    """One protocol line; anything that is not a JSON object reads as ``{}``."""
    try:
        message = json.loads(line)
    except ValueError:
        return {}
    return message if isinstance(message, dict) else {}


async def _report_progress(on_progress: Callable[[float], None], percent: object) -> None:
    if not isinstance(percent, int | float):
        return
    try:
        await asyncio.to_thread(on_progress, float(percent))
    except Exception:  # pragma: no cover  defensive
        _logger.exception("ytdlp_progress_hook_error")
