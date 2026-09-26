"""Direct CDN download for images yt-dlp resolves but cannot download.

yt-dlp has no image formats for Instagram photos: a photo post or a photo
item in a carousel carries only ``thumbnails`` (the ``image_versions2``
candidates). The provider picks the image URL and this fetcher streams it
into the job directory under the same host allowlist as yt-dlp.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from app.exceptions import DownloadError, FileTooLargeError
from app.logging_config import get_logger
from app.utils.url import is_allowed_host

_logger = get_logger(__name__)

_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}
_TIMEOUT_S = 30.0
_MAX_REDIRECTS = 5


class HttpImageFetcher:
    def __init__(
        self,
        *,
        proxy_url: str | None,
        max_bytes: int,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._proxy_url = (proxy_url or "").strip() or None
        self._max_bytes = max_bytes
        self._headers = headers or {}
        self._transport = transport

    async def fetch(self, url: str, *, target_dir: Path, stem: str) -> Path:
        """Stream ``url`` into ``target_dir/<stem><ext>`` and return the path.

        The extension comes from the response ``Content-Type``; anything
        that is not an image is rejected.
        """
        if not is_allowed_host(url):
            raise DownloadError(f"Image URL host is not allowed: {url[:80]!r}")
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            async with (
                self._client() as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                ext = _image_ext(response.headers.get("Content-Type"))
                declared = response.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > self._max_bytes:
                    raise FileTooLargeError(f"Image is {declared} bytes")
                path = target_dir / f"{stem}{ext}"
                try:
                    written = await self._write(response, path)
                except BaseException:
                    path.unlink(missing_ok=True)
                    raise
        except httpx.HTTPError as exc:
            raise DownloadError(f"Image download failed: {exc}") from exc
        _logger.info("image_downloaded", size_bytes=written, ext=ext)
        return path

    async def _write(self, response: httpx.Response, path: Path) -> int:
        written = 0
        with path.open("wb") as fh:
            async for chunk in response.aiter_bytes():
                written += len(chunk)
                if written > self._max_bytes:
                    raise FileTooLargeError(f"Image exceeds {self._max_bytes} bytes")
                fh.write(chunk)
        if written == 0:
            raise DownloadError("Image download returned an empty body")
        return written

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self._headers,
            timeout=_TIMEOUT_S,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            proxy=self._proxy_url,
            transport=self._transport,
            event_hooks={"request": [_reject_disallowed_host]},
        )


async def _reject_disallowed_host(request: httpx.Request) -> None:
    # Runs for every hop, so a CDN redirect cannot leave the allowlist.
    if not is_allowed_host(str(request.url)):
        raise DownloadError(f"Image redirect to disallowed host {request.url.host!r}")


def _image_ext(content_type: str | None) -> str:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    ext = _EXT_BY_MIME.get(mime)
    if ext is None:
        raise DownloadError(f"Unexpected image content type: {mime or 'missing'}")
    return ext
