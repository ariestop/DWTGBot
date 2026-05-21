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
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from app.config import Settings
from app.exceptions import (
    DownloadError,
    DownloadTimeoutError,
    MediaNotFoundError,
    MediaPrivateError,
    ProviderError,
    UpstreamUnavailableError,
)
from app.infrastructure.cache.redis_circuit_breaker import RedisCircuitBreaker
from app.logging_config import get_logger
from app.utils.url import is_allowed_host

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


# Video containers we post-process after download. Limited to MP4-family
# formats because ``-movflags +faststart`` only applies to ISO BMFF
# containers and Telegram's mobile players expect MP4.
_FASTSTART_EXTS = frozenset({".mp4", ".mov", ".m4v"})
_ALLOWED_EXTRACTORS = ("Youtube", "YoutubeTab", "Instagram")
_URL_MATCH_KEYS = ("webpage_url", "url", "original_url")

# Mobile Telegram (iOS/Android) decodes through hardware codecs that
# require a very narrow subset of formats. Anything else plays on
# desktop (ffmpeg-based) but freezes the video track on phones while
# audio decodes normally -- the exact "frozen frame, sound works"
# symptom users reported for VP9-in-MP4 (common for YouTube 1080p) and
# HEVC-in-MP4 (sometimes served by Instagram). Telegram's own file
# spec (https://core.telegram.org/api/files#video) lists H.264 + AAC
# as the expected pair.
_MOBILE_OK_VCODECS = frozenset({"h264", "avc1"})
_MOBILE_OK_ACODECS = frozenset({"aac"})
_MOBILE_OK_PIX_FMTS = frozenset({"yuv420p", "yuvj420p"})


async def _probe_video_codecs(
    path: Path, ffprobe_bin: str
) -> tuple[str | None, str | None, str | None]:
    """Return ``(video_codec, audio_codec, pix_fmt)`` — ``None`` if unknown."""
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,pix_fmt",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            return (None, None, None)
        payload = json.loads(stdout.decode("utf-8", errors="replace") or "{}")
    except (TimeoutError, ValueError, json.JSONDecodeError, OSError) as exc:
        _logger.warning("probe_codecs_failed", file=str(path), error=repr(exc))
        return (None, None, None)
    vcodec = acodec = pix_fmt = None
    for stream in payload.get("streams") or []:
        if stream.get("codec_type") == "video" and vcodec is None:
            vcodec = stream.get("codec_name")
            pix_fmt = stream.get("pix_fmt")
        elif stream.get("codec_type") == "audio" and acodec is None:
            acodec = stream.get("codec_name")
    return (vcodec, acodec, pix_fmt)


async def _ensure_mobile_compatible(
    path: Path,
    ffmpeg_bin: str,
    ffprobe_bin: str | None,
    *,
    force_transcode: bool = False,
) -> None:
    """Rewrite ``path`` so Telegram mobile clients can play it.

    Fast path (when the probe shows H.264 video + AAC audio + yuv420p):
    ``-c copy -movflags +faststart``, a byte-level container rewrite
    that moves ``moov`` to the head. Completes in well under a second
    for sub-100 MB files and preserves the original quality exactly.

    Slow path (anything else -- VP9, HEVC, Opus, yuv444p, etc.):
    transcode to H.264 Main@4.0 + AAC 192 kbps + yuv420p + faststart.
    ``-preset veryfast`` keeps encoding time close to real-time on
    2 vCPU at 1080p. Quality is visually indistinguishable at typical
    Telegram viewing sizes. CRF 23 is libx264's default sweet spot.

    ``force_transcode`` routes into the slow path unconditionally. Use
    it for sources where codec names look fine to ffprobe but the
    container still isn't mobile-safe (e.g. Instagram's fragmented
    MP4, High@5.x profile, unusual GOPs) -- a belt-and-suspenders
    remux to a known-good mp4 reliably fixes the "frozen first frame"
    symptom on phones.

    Any failure leaves the original file untouched (we log and move
    on) so a post-step regression cannot break downloads that used to
    reach the user.
    """
    # Without ffprobe we can't tell whether a transcode is needed; err on
    # the side of the cheap remux (matches the pre-probe behaviour and
    # still fixes the moov-at-end case for H.264 sources).
    vcodec = acodec = pix_fmt = None
    if ffprobe_bin:
        vcodec, acodec, pix_fmt = await _probe_video_codecs(path, ffprobe_bin)
    needs_transcode = force_transcode or (
        (vcodec is not None and vcodec not in _MOBILE_OK_VCODECS)
        or (acodec is not None and acodec not in _MOBILE_OK_ACODECS)
        or (pix_fmt is not None and pix_fmt not in _MOBILE_OK_PIX_FMTS)
    )

    # Keep the original extension as the *last* suffix of the tmp file
    # (e.g. ``video.mp4`` → ``video.remux.tmp.mp4``). ffmpeg's muxer
    # auto-detection looks at the trailing extension only; if it were
    # ``.tmp`` ffmpeg would either fail to pick a muxer or pick the
    # wrong one, at which point ``-movflags +faststart`` becomes
    # "Invalid argument" and the whole remux fails.
    tmp = path.with_name(path.stem + ".remux.tmp" + path.suffix)
    if needs_transcode:
        # Tuned for Telegram mobile clients:
        #   * high@4.1 is the widest-supported H.264 profile/level combo
        #     that covers 1080p30 on every shipping iOS/Android decoder;
        #     main@4.0 (previous value) overflows for 1080p60 sources
        #     (common on Instagram) and some decoders refuse to start on
        #     level-non-conforming streams.
        #   * ``-r 30`` caps the frame rate per Telegram's spec ("max 30
        #     fps") — dropping to 30 fps is cheap at ``veryfast`` and
        #     avoids the 60-fps mobile-decoder stall altogether.
        #   * 48 kHz / stereo AAC is the codec pair Telegram's own
        #     uploader normalises to; it avoids edge cases with 44.1 kHz
        #     or mono tracks on older Androids.
        #
        # We deliberately do NOT set ``-bsf:v dump_extra``: the MP4
        # muxer stores SPS/PPS in the ``avcC`` box automatically and
        # rejects ("Error initializing the muxer: Invalid argument")
        # streams that also carry them inline. ``dump_extra`` is meant
        # for raw/TS outputs.
        args = [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "23",
            "-r",
            "30",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
        ]
        event_ok = "mobile_transcode_done"
        event_err = "mobile_transcode_failed"
        timeout = 600
    else:
        args = ["-c", "copy", "-movflags", "+faststart"]
        event_ok = "faststart_remux_done"
        event_err = "faststart_remux_failed"
        timeout = 120

    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0",
            *args,
            str(tmp),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tail = (stderr or b"").decode("utf-8", errors="replace").strip().splitlines()[-3:]
            _logger.warning(
                event_err,
                file=str(path),
                returncode=proc.returncode,
                stderr=" | ".join(tail),
                vcodec=vcodec,
                acodec=acodec,
                pix_fmt=pix_fmt,
            )
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            return
        tmp.replace(path)
        _logger.info(
            event_ok,
            file=str(path),
            vcodec=vcodec,
            acodec=acodec,
            pix_fmt=pix_fmt,
        )
    except (TimeoutError, OSError) as exc:
        _logger.warning(event_err, file=str(path), error=repr(exc))
        if tmp.exists():
            tmp.unlink(missing_ok=True)


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
        self._apply_source_guards(opts)
        if extra_opts:
            opts.update(extra_opts)
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
        host = _host_of(url)
        await self._trip_if_open(host)
        target_dir.mkdir(parents=True, exist_ok=True)

        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            # noprogress stays True to keep yt-dlp from spamming stderr
            # with ANSI redraws; the Python-level progress_hook below is
            # the authoritative progress surface and is unaffected by
            # this flag (see yt-dlp source: YoutubeDL.to_screen is the
            # gated path, not the hook dispatcher).
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
        self._apply_source_guards(opts)
        if on_progress is not None:
            opts["progress_hooks"] = [_make_progress_hook(on_progress)]
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
            await asyncio.wait_for(
                asyncio.to_thread(self._download_sync, url, opts),
                timeout=download_timeout,
            )
        except TimeoutError as exc:
            _logger.warning(
                "ytdlp_download_timeout",
                url=url,
                timeout_s=download_timeout,
            )
            raise DownloadTimeoutError(f"yt-dlp timed out after {download_timeout}s") from exc
        except YtDlpDownloadError as exc:
            await self._report_if_throttle(host, exc)
            raise self._classify(exc) from exc
        except Exception as exc:
            _logger.exception("ytdlp_download_unexpected", url=url)
            raise DownloadError(f"yt-dlp download failed: {exc}") from exc

        await self._note_success(host)
        files = sorted(p for p in target_dir.iterdir() if p.is_file())
        # Post-step: make MP4 outputs mobile-Telegram-friendly. Beyond the
        # ``faststart`` fix (moov atom at the head so the player can start
        # before the file is fully downloaded), mobile clients also need
        # H.264 + AAC + yuv420p — VP9-in-MP4 (common for YouTube 1080p)
        # and HEVC-in-MP4 (sometimes Instagram) decode fine on desktop
        # but freeze the video track on iOS/Android while audio plays.
        # ``_ensure_mobile_compatible`` probes the container and chooses
        # the cheapest path that keeps the output playable on phones.
        resolved_ffprobe = shutil.which(self._settings.FFPROBE_BIN) if resolved_ffmpeg else None
        if resolved_ffmpeg:
            for f in files:
                if f.suffix.lower() in _FASTSTART_EXTS:
                    await _ensure_mobile_compatible(
                        f,
                        resolved_ffmpeg,
                        resolved_ffprobe,
                        force_transcode=force_transcode,
                    )
        return files

    async def probe_size(
        self,
        url: str,
        *,
        format_spec: str,
        extra_opts: dict[str, Any] | None = None,
    ) -> int | None:
        """Best-effort size probe without downloading bytes."""
        host = _host_of(url)
        await self._trip_if_open(host)
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "simulate": True,
            "format": format_spec,
            "socket_timeout": 30,
            "retries": 1,
            "noplaylist": False,
        }
        self._apply_source_guards(opts)
        if extra_opts:
            opts.update(extra_opts)
        self._apply_proxy(opts)
        try:
            result = await asyncio.to_thread(self._extract_sync, url, opts)
        except YtDlpDownloadError as exc:
            await self._report_if_throttle(host, exc)
            raise self._classify(exc) from exc
        except Exception as exc:
            _logger.exception("ytdlp_probe_unexpected", url=url)
            raise DownloadError(f"yt-dlp size probe failed: {exc}") from exc
        await self._note_success(host)
        return _extract_known_size(result)

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

    def _apply_source_guards(self, opts: dict[str, Any]) -> None:
        """A17: constrain yt-dlp to supported extractors and hosts."""
        opts.setdefault("allowed_extractors", list(_ALLOWED_EXTRACTORS))
        opts.setdefault("match_filter", _match_allowed_host)

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
            or "sign in to confirm" in msg
            or "use --cookies" in msg
            or "use --cookies-from-browser" in msg
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


def _make_progress_hook(
    on_progress: Callable[[float], None],
) -> Callable[[dict[str, Any]], None]:
    """Wrap ``on_progress`` into yt-dlp's ``progress_hooks`` protocol.

    yt-dlp emits dicts like ``{"status": "downloading", "downloaded_bytes":
    123, "total_bytes": 456, "_percent_str": " 25.0%", ...}``. The
    ``_percent_str`` key is the most portable: yt-dlp computes it from
    whichever total field is actually populated (``total_bytes``,
    ``total_bytes_estimate``, or a manifest-derived count) and formats
    it as a percentage ready for display. We parse it and forward as a
    float; if the string is missing or malformed we fall back to
    computing from bytes counters. Hook errors are logged and
    swallowed to preserve the download.
    """

    def _hook(d: dict[str, Any]) -> None:
        try:
            if d.get("status") != "downloading":
                return
            percent = _extract_percent(d)
            if percent is None:
                return
            on_progress(percent)
        except Exception:  # pragma: no cover  defensive
            _logger.exception("ytdlp_progress_hook_error")

    return _hook


def _extract_percent(d: dict[str, Any]) -> float | None:
    """Pull a 0..100 percent out of a yt-dlp progress dict.

    Priority order mirrors yt-dlp's own internal computation so the
    hook reports the same values users saw on the terminal:

    1. ``_percent_str`` — best signal, pre-formatted (e.g. ``" 25.0%"``).
    2. ``downloaded_bytes`` / ``total_bytes`` — exact when available.
    3. ``downloaded_bytes`` / ``total_bytes_estimate`` — approximate for
       HLS/DASH.
    4. None — caller skips the write.
    """
    raw = d.get("_percent_str")
    if isinstance(raw, str):
        cleaned = raw.strip().rstrip("%").strip()
        try:
            return float(cleaned)
        except ValueError:
            pass

    downloaded = d.get("downloaded_bytes")
    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    if isinstance(downloaded, int | float) and isinstance(total, int | float) and total > 0:
        return max(0.0, min(100.0, (downloaded / total) * 100.0))

    return None


def _match_allowed_host(info_dict: dict[str, Any]) -> str | None:
    """Reject entries whose resolved URL escapes our provider/CDN allowlist."""
    for key in _URL_MATCH_KEYS:
        raw = info_dict.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        if is_allowed_host(raw):
            return None
        return f"Disallowed host for URL {raw!r}"
    return None


def _extract_known_size(payload: dict[str, Any]) -> int | None:
    """Best-effort filesize extraction from a yt-dlp info dict."""
    entry = payload["entries"][0] if payload.get("entries") else payload
    requested_downloads = entry.get("requested_downloads")
    if isinstance(requested_downloads, list) and requested_downloads:
        total = _sum_known_sizes(requested_downloads)
        if total is not None:
            return total
    requested_formats = entry.get("requested_formats")
    if isinstance(requested_formats, list) and requested_formats:
        total = _sum_known_sizes(requested_formats)
        if total is not None:
            return total
    return _single_known_size(entry)


def _sum_known_sizes(items: list[dict[str, Any]]) -> int | None:
    total = 0
    saw_any = False
    for item in items:
        size = _single_known_size(item)
        if size is None:
            return None
        total += size
        saw_any = True
    return total if saw_any else None


def _single_known_size(item: dict[str, Any]) -> int | None:
    for key in ("filesize", "filesize_approx"):
        raw = item.get(key)
        if isinstance(raw, int | float) and raw > 0:
            return int(raw)
    return None
