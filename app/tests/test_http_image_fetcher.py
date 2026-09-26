"""HttpImageFetcher: host allowlist, content type and size limits."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.exceptions import DownloadError, FileTooLargeError
from app.infrastructure.downloader.http_image import HttpImageFetcher

_URL = "https://scontent-iad3-1.cdninstagram.com/v/t39.30808-6/photo.jpg"


def _fetcher(handler: httpx.MockTransport, *, max_bytes: int = 1024) -> HttpImageFetcher:
    return HttpImageFetcher(proxy_url=None, max_bytes=max_bytes, transport=handler)


@pytest.mark.asyncio
async def test_fetch_writes_image_with_extension_from_content_type(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"jpeg")
    )

    path = await _fetcher(transport).fetch(_URL, target_dir=tmp_path, stem="DdrzVBqDScp")

    assert path == tmp_path / "DdrzVBqDScp.jpg"
    assert path.read_bytes() == b"jpeg"


@pytest.mark.asyncio
async def test_fetch_rejects_url_outside_allowlist(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200))

    with pytest.raises(DownloadError):
        await _fetcher(transport).fetch(
            "http://169.254.169.254/latest", target_dir=tmp_path, stem="x"
        )


@pytest.mark.asyncio
async def test_fetch_rejects_redirect_outside_allowlist(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith("cdninstagram.com"):
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"leak")

    with pytest.raises(DownloadError):
        await _fetcher(httpx.MockTransport(handler)).fetch(_URL, target_dir=tmp_path, stem="x")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_fetch_rejects_non_image_response(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"Content-Type": "text/html"}, content=b"<html>"
        )
    )

    with pytest.raises(DownloadError):
        await _fetcher(transport).fetch(_URL, target_dir=tmp_path, stem="x")


@pytest.mark.asyncio
async def test_fetch_removes_partial_file_when_image_exceeds_cap(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"Content-Type": "image/jpeg"}, content=b"x" * 2048
        )
    )

    with pytest.raises(FileTooLargeError):
        await _fetcher(transport, max_bytes=1024).fetch(_URL, target_dir=tmp_path, stem="big")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_fetch_maps_http_error_to_download_error(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403))

    with pytest.raises(DownloadError):
        await _fetcher(transport).fetch(_URL, target_dir=tmp_path, stem="x")
