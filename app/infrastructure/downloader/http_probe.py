"""HEAD-based size probe for media URLs yt-dlp resolved but did not size."""

from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from app.utils.url import is_allowed_host


def head_content_length(url: str, *, proxy_url: str | None) -> int | None:
    if not is_allowed_host(url):
        return None
    handlers = []
    if proxy_url:
        handlers.append(ProxyHandler({"http": proxy_url, "https": proxy_url}))
    opener = build_opener(*handlers)
    request = Request(url, method="HEAD")  # noqa: S310 - host allowlist enforced above
    try:
        with opener.open(request, timeout=30) as response:
            value = response.headers.get("Content-Length")
    except (HTTPError, URLError, OSError, ValueError):
        return None
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None
