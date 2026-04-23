"""URL detection and platform routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.domain.enums import Platform
from app.exceptions import InvalidUrlError, UnsupportedPlatformError

_URL_RE = re.compile(
    r"https?://[^\s<>\"]+",
    re.IGNORECASE,
)

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}

_INSTAGRAM_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
}

_YTDLP_ALLOWED_HOST_SUFFIXES = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "googlevideo.com",
        "ytimg.com",
        "ggpht.com",
        "instagram.com",
        "cdninstagram.com",
        "fbcdn.net",
    }
)


@dataclass(frozen=True, slots=True)
class DetectedUrl:
    raw: str
    normalized: str
    platform: Platform


def extract_first_url(text: str) -> str | None:
    """Return the first URL found in arbitrary text, or None."""
    if not text:
        return None
    match = _URL_RE.search(text)
    return match.group(0) if match else None


def detect_platform(url: str) -> Platform:
    """Return the supported platform for a URL or raise."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise InvalidUrlError(str(exc)) from exc

    if not parsed.scheme or not parsed.netloc:
        raise InvalidUrlError("URL is missing scheme or host")

    host = parsed.netloc.lower()
    if host in _YOUTUBE_HOSTS:
        return Platform.YOUTUBE
    if host in _INSTAGRAM_HOSTS:
        return Platform.INSTAGRAM
    raise UnsupportedPlatformError(f"Host not supported: {host}")


def normalize_domain(url: str) -> str | None:
    """Best-effort eTLD+1 normalisation for rate-limit keys.

    Used by the L4 (per-domain) layer in ``docs/36-rate-limiting.md``. We
    don't pull in ``tldextract`` for two known providers — keep the map
    explicit so adding a provider also touches this file (P2: no
    invisible coupling). Returns ``None`` for malformed URLs; the caller
    treats that as "skip L4".
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.netloc or "").lower().split(":", 1)[0]
    if not host:
        return None
    if host in _YOUTUBE_HOSTS:
        return "youtube.com"
    if host in _INSTAGRAM_HOSTS:
        return "instagram.com"
    # Strip a single leading "www." for unknown hosts; do NOT attempt full
    # PSL parsing — the limiter is best-effort for unknown providers.
    return host[4:] if host.startswith("www.") else host


def is_allowed_host(url: str) -> bool:
    """Return ``True`` iff ``url`` resolves to a supported provider/CDN host.

    Used by yt-dlp SSRF guards: once the extractor starts following redirects,
    the host can legitimately shift from the user-facing domain to a provider
    CDN (for example ``*.googlevideo.com`` or ``*.cdninstagram.com``). We
    therefore allow a curated suffix list rather than the narrower intake-side
    platform detector.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False
    return any(
        host == suffix or host.endswith("." + suffix) for suffix in _YTDLP_ALLOWED_HOST_SUFFIXES
    )


def detect(url_or_text: str) -> DetectedUrl:
    """High-level detect: accepts free-form text or a bare URL."""
    candidate = url_or_text.strip()
    if not candidate:
        raise InvalidUrlError("Empty input")

    url = (
        candidate if candidate.startswith(("http://", "https://")) else extract_first_url(candidate)
    )
    if not url:
        raise InvalidUrlError("No URL found in message")

    platform = detect_platform(url)
    return DetectedUrl(raw=candidate, normalized=url, platform=platform)
