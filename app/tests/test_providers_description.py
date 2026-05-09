"""Providers fill MediaInfo.description from yt-dlp sanitized dicts."""

from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings, get_settings
from app.domain.enums import MediaKind, Platform
from app.infrastructure.downloader.ytdlp_runner import YtDlpRunner
from app.infrastructure.providers.instagram import InstagramProvider
from app.infrastructure.providers.youtube import YouTubeProvider
from app.infrastructure.storage.local_storage import LocalStorage


class _FakeYtDlp(YtDlpRunner):
    """Tiny stub that returns a canned extract_info response without the
    network round-trip. Inherits from YtDlpRunner so type-checks stay
    happy (YouTubeProvider accepts YtDlpRunner)."""

    def __init__(self, settings: Settings, raw: dict[str, Any]) -> None:
        super().__init__(settings)
        self._raw = raw

    async def extract_info(
        self,
        url: str,
        *,
        extra_opts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:  # type: ignore[override]
        del url, extra_opts
        return self._raw


def _youtube_provider(raw: dict[str, Any]) -> YouTubeProvider:
    s = get_settings()
    return YouTubeProvider(settings=s, ytdlp=_FakeYtDlp(s, raw), storage=LocalStorage(s))


def _instagram_provider(raw: dict[str, Any]) -> InstagramProvider:
    s = get_settings()
    return InstagramProvider(settings=s, ytdlp=_FakeYtDlp(s, raw), storage=LocalStorage(s))


# ---------- YouTube ----------


@pytest.mark.asyncio
async def test_youtube_description_populated() -> None:
    provider = _youtube_provider(
        {
            "id": "abc",
            "title": "Hello",
            "description": "First line.\nSecond line.",
            "formats": [{"vcodec": "avc1.64001F", "height": 720}],
            "duration": 42.0,
        }
    )
    info = await provider.get_info("https://youtube.com/watch?v=abc")
    assert info.platform is Platform.YOUTUBE
    assert info.kind is MediaKind.VIDEO
    assert info.description == "First line.\nSecond line."


@pytest.mark.asyncio
async def test_youtube_description_missing_is_empty_string() -> None:
    provider = _youtube_provider(
        {
            "id": "abc",
            "title": "Hello",
            "formats": [{"vcodec": "avc1.64001F", "height": 720}],
        }
    )
    info = await provider.get_info("https://youtube.com/watch?v=abc")
    assert info.description == ""


@pytest.mark.asyncio
async def test_youtube_description_truncated() -> None:
    long = "abcdef " * 3000  # ~21000 chars; default POST_TEXT_MAX_CHARS=10000
    provider = _youtube_provider(
        {
            "id": "abc",
            "title": "Hello",
            "description": long,
            "formats": [{"vcodec": "avc1.64001F", "height": 720}],
        }
    )
    info = await provider.get_info("https://youtube.com/watch?v=abc")
    settings = get_settings()
    assert len(info.description) <= settings.POST_TEXT_MAX_CHARS
    assert info.description.endswith("…")


@pytest.mark.asyncio
async def test_youtube_description_whitespace_only_collapses() -> None:
    provider = _youtube_provider(
        {
            "id": "abc",
            "title": "Hello",
            "description": "   \n\n   ",
            "formats": [{"vcodec": "avc1.64001F", "height": 720}],
        }
    )
    info = await provider.get_info("https://youtube.com/watch?v=abc")
    assert info.description == ""


# ---------- Instagram ----------


@pytest.mark.asyncio
async def test_instagram_description_populated() -> None:
    provider = _instagram_provider(
        {
            "id": "ig_1",
            "title": "Nice reel",
            "description": "Caption with emoji 🎉 #tag @user",
            "ext": "mp4",
            "vcodec": "avc1",
            "duration": 12.0,
        }
    )
    info = await provider.get_info("https://instagram.com/reel/ig_1/")
    assert info.platform is Platform.INSTAGRAM
    assert info.description == "Caption with emoji 🎉 #tag @user"


@pytest.mark.asyncio
async def test_instagram_description_missing_is_empty() -> None:
    provider = _instagram_provider(
        {
            "id": "ig_2",
            "title": "Photo post",
            "ext": "jpg",
        }
    )
    info = await provider.get_info("https://instagram.com/p/ig_2/")
    assert info.description == ""
