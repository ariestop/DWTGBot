"""Interpretation of yt-dlp outcomes: error classes, throttling, sizes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from yt_dlp.utils import DownloadError as YtDlpDownloadError

from app.exceptions import (
    DownloadError,
    MediaNotFoundError,
    MediaPrivateError,
    ProviderError,
    UpstreamUnavailableError,
)

# Narrow on purpose: counting generic 5xx / network errors as throttles
# would open the circuit breaker on unrelated blips.
_THROTTLE_MARKERS = (
    "http error 429",
    "too many requests",
    "rate limit",
    "rate-limit",
    "rate limited",
)

_PRIVATE_MARKERS = (
    "private",
    "login required",
    "sign in to confirm",
    "use --cookies",
)
_NOT_FOUND_MARKERS = ("not found", "does not exist", "removed", "404")
_SIZE_KEYS = ("filesize", "filesize_approx")


def looks_like_throttle(exc: YtDlpDownloadError) -> bool:
    """Does this error look like upstream is rate-limiting us?"""
    msg = str(exc).lower()
    return any(marker in msg for marker in _THROTTLE_MARKERS)


def classify_error(exc: YtDlpDownloadError) -> ProviderError | DownloadError:
    """Map a yt-dlp error onto the project's exception hierarchy."""
    msg = str(exc).lower()
    # Before the private markers: Instagram's anonymous rate-limit answer
    # ("redirected to the login page. You have exceeded the rate-limit...
    # Use --cookies") is about our egress IP, not about the post.
    if looks_like_throttle(exc):
        return UpstreamUnavailableError(str(exc))
    if any(marker in msg for marker in _PRIVATE_MARKERS):
        return MediaPrivateError(str(exc))
    if any(marker in msg for marker in _NOT_FOUND_MARKERS):
        return MediaNotFoundError(str(exc))
    if "unsupported url" in msg:
        return ProviderError(str(exc))
    return DownloadError(str(exc))


def extract_known_size(payload: Mapping[str, Any]) -> int | None:
    """Best-effort total filesize from a yt-dlp info dict.

    Prefers the formats yt-dlp actually selected (``requested_downloads``,
    then ``requested_formats``, summed for merged video+audio), then the
    entry's own size. Any unknown part makes the sum unknown.
    """
    entries = payload.get("entries")
    entry: Mapping[str, Any] = entries[0] if entries else payload
    for key in ("requested_downloads", "requested_formats"):
        items = entry.get(key)
        if isinstance(items, list) and items:
            total = _sum_known_sizes(items)
            if total is not None:
                return total
    return _single_known_size(entry)


def selected_media_urls(payload: Mapping[str, Any]) -> list[str]:
    """Direct URLs of the formats yt-dlp selected, across all entries.

    Used to HEAD-probe sizes when the extractor reports none (Instagram's
    anonymous DASH formats carry neither ``filesize`` nor ``duration``).
    Returns ``[]`` when any entry has no direct URL, because a partial
    sum would understate the size.
    """
    entries = payload.get("entries") or [payload]
    urls: list[str] = []
    for entry in entries:
        selected = entry.get("requested_downloads") or entry.get("requested_formats")
        if isinstance(selected, list) and selected:
            entry_urls = [item.get("url") for item in selected]
        else:
            entry_urls = [entry.get("url")]
        if not all(isinstance(u, str) and u for u in entry_urls):
            return []
        urls.extend(entry_urls)
    return urls


def _sum_known_sizes(items: Sequence[Mapping[str, Any]]) -> int | None:
    total = 0
    for item in items:
        size = _single_known_size(item)
        if size is None:
            return None
        total += size
    return total if items else None


def _single_known_size(item: Mapping[str, Any]) -> int | None:
    for key in _SIZE_KEYS:
        raw = item.get(key)
        if isinstance(raw, int | float) and raw > 0:
            return int(raw)
    return None
