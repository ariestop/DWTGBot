"""YouTube :meth:`YouTubeProvider.default_option` — no network."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.entities.media_info import MediaInfo
from app.domain.enums import MediaKind, Platform
from app.exceptions import DownloadError
from app.infrastructure.downloader.ytdlp_runner import YtDlpRunner
from app.infrastructure.providers.youtube import YouTubeProvider
from app.infrastructure.storage.local_storage import LocalStorage


def _provider() -> YouTubeProvider:
    s = get_settings()
    return YouTubeProvider(settings=s, ytdlp=YtDlpRunner(s), storage=LocalStorage(s))


def _info(heights: list[int], duration: float | None = 60.0) -> MediaInfo:
    return MediaInfo(
        platform=Platform.YOUTUBE,
        media_id="abc",
        title="Test",
        kind=MediaKind.VIDEO,
        duration_sec=duration,
        raw={"available_heights": heights, "duration": duration},
    )


def test_picks_highest_available_bucket_1080() -> None:
    opt = _provider().default_option(_info([1080]))
    assert opt.kind is MediaKind.VIDEO
    assert opt.height == 1080
    assert opt.key == "video_1080"


def test_picks_highest_bucket_when_below_1080() -> None:
    opt = _provider().default_option(_info([360, 480]))
    assert opt.height == 480
    assert opt.key == "video_480"


@pytest.mark.parametrize(
    "heights,expected_height",
    [
        ([360], 360),
        ([360, 720], 720),
        ([240, 360, 480, 720, 1080], 1080),
        ([1440, 2160], 1080),  # Buckets cap at 1080 even if 4K source is available.
    ],
)
def test_picks_highest_within_known_buckets(heights: list[int], expected_height: int) -> None:
    opt = _provider().default_option(_info(heights))
    assert opt.height == expected_height


def test_raises_when_no_video_formats() -> None:
    with pytest.raises(DownloadError):
        _provider().default_option(_info(heights=[]))


def test_raises_on_audio_only_source() -> None:
    # MediaInfo with no video heights — default_option must NOT silently
    # fall back to audio_mp3 (ADR-0010 §2.1).
    info = _info(heights=[])
    with pytest.raises(DownloadError):
        _provider().default_option(info)


def test_default_option_is_subset_of_build_options() -> None:
    info = _info([720, 1080])
    default = _provider().default_option(info)
    all_opts = _provider().build_options(info)
    # Key + kind + height must match a bucket from build_options so the
    # existing download pipeline resolves it unchanged.
    assert any(
        o.key == default.key and o.kind is default.kind and o.height == default.height
        for o in all_opts
    )
