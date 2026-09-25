"""Pure builders for yt-dlp option dicts and hooks."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlparse

from app.logging_config import get_logger
from app.utils.url import is_allowed_host

_logger = get_logger(__name__)

YtDlpOpts = dict[str, Any]

ALLOWED_EXTRACTORS = ("Youtube", "YoutubeTab", "Instagram")
_URL_MATCH_KEYS = ("webpage_url", "url", "original_url")


class ProgressEvent(TypedDict, total=False):
    """The subset of yt-dlp's ``progress_hooks`` payload we read."""

    status: str
    _percent_str: str
    downloaded_bytes: int | float
    total_bytes: int | float
    total_bytes_estimate: int | float


def extract_opts() -> YtDlpOpts:
    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
        "extract_flat": False,
        "socket_timeout": 30,
        "retries": 1,
    }


def probe_opts(format_spec: str) -> YtDlpOpts:
    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "simulate": True,
        "format": format_spec,
        "socket_timeout": 30,
        "retries": 1,
        "noplaylist": False,
    }


def download_opts(format_spec: str, target_dir: Path) -> YtDlpOpts:
    return {
        "quiet": True,
        "no_warnings": True,
        # noprogress keeps yt-dlp from spamming stderr with ANSI redraws;
        # the Python-level progress hook is unaffected by this flag
        # (YoutubeDL.to_screen is the gated path, not the hook dispatcher).
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


def apply_source_guards(opts: YtDlpOpts) -> None:
    """A17: constrain yt-dlp to supported extractors and hosts."""
    opts.setdefault("allowed_extractors", list(ALLOWED_EXTRACTORS))
    opts.setdefault("match_filter", match_allowed_host)


def apply_proxy(opts: YtDlpOpts, proxy_url: str | None) -> None:
    """S9: inject ``HTTPS_PROXY_URL`` unless ``extra_opts`` already set one.

    yt-dlp accepts both ``http(s)://`` and ``socks5://`` schemes via the
    same ``proxy`` key; a provider-supplied proxy is a per-request override.
    """
    if "proxy" in opts:
        return
    proxy = (proxy_url or "").strip()
    if proxy:
        opts["proxy"] = proxy


def match_allowed_host(info_dict: dict[str, Any]) -> str | None:
    """Reject entries whose resolved URL escapes our provider/CDN allowlist."""
    for key in _URL_MATCH_KEYS:
        raw = info_dict.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        if is_allowed_host(raw):
            return None
        return f"Disallowed host for URL {raw!r}"
    return None


def host_of(url: str) -> str:
    """Circuit-breaker key: lower-cased host without ``www.``.

    ``www.youtube.com`` and ``youtube.com`` share one breaker because the
    same upstream policy applies. ``""`` disables tracking for the call.
    """
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    host = host.lower()
    return host.removeprefix("www.")


def make_progress_hook(
    on_progress: Callable[[float], None],
) -> Callable[[ProgressEvent], None]:
    """Wrap ``on_progress`` into yt-dlp's ``progress_hooks`` protocol.

    Runs inside the ``asyncio.to_thread`` worker. Hook errors are logged
    and swallowed: yt-dlp has no recovery path for them and progress
    reporting must never abort a download (ADR-0010 §2.2).
    """

    def _hook(d: ProgressEvent) -> None:
        try:
            if d.get("status") != "downloading":
                return
            percent = extract_percent(d)
            if percent is None:
                return
            on_progress(percent)
        except Exception:  # pragma: no cover  defensive
            _logger.exception("ytdlp_progress_hook_error")

    return _hook


def extract_percent(d: ProgressEvent) -> float | None:
    """Pull a 0..100 percent out of a yt-dlp progress dict.

    Priority mirrors yt-dlp's own computation so the hook reports the
    values users would see in a terminal:

    1. ``_percent_str`` (e.g. ``" 25.0%"``), computed from whichever total
       yt-dlp actually has.
    2. ``downloaded_bytes`` / ``total_bytes`` — exact when available.
    3. ``downloaded_bytes`` / ``total_bytes_estimate`` — approximate for
       HLS/DASH.
    4. ``None`` — the caller skips the write.
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
