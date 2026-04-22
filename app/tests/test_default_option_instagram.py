"""Instagram :meth:`InstagramProvider.default_option` — no network."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.entities.media_info import MediaInfo, MediaItem
from app.domain.enums import MediaKind, Platform
from app.exceptions import DownloadError
from app.infrastructure.downloader.ytdlp_runner import YtDlpRunner
from app.infrastructure.providers.instagram import InstagramProvider
from app.infrastructure.storage.local_storage import LocalStorage


def _provider() -> InstagramProvider:
    s = get_settings()
    return InstagramProvider(settings=s, ytdlp=YtDlpRunner(s), storage=LocalStorage(s))


def _single_video_info() -> MediaInfo:
    return MediaInfo(
        platform=Platform.INSTAGRAM,
        media_id="reel_1",
        title="Reel",
        kind=MediaKind.VIDEO,
        items=(MediaItem(kind=MediaKind.VIDEO, url="https://x/v.mp4"),),
        raw={"is_gallery": False, "kinds": ["video"]},
    )


def _single_photo_info() -> MediaInfo:
    return MediaInfo(
        platform=Platform.INSTAGRAM,
        media_id="photo_1",
        title="Photo",
        kind=MediaKind.PHOTO,
        items=(MediaItem(kind=MediaKind.PHOTO, url="https://x/p.jpg"),),
        raw={"is_gallery": False, "kinds": ["photo"]},
    )


def _gallery_info(kinds: tuple[MediaKind, ...]) -> MediaInfo:
    items = tuple(MediaItem(kind=k, url=f"https://x/{i}") for i, k in enumerate(kinds))
    return MediaInfo(
        platform=Platform.INSTAGRAM,
        media_id="carousel_1",
        title="Carousel",
        kind=MediaKind.GALLERY,
        items=items,
        raw={"is_gallery": True, "kinds": [k.value for k in kinds]},
    )


def test_single_video_picks_single_video_option() -> None:
    opt = _provider().default_option(_single_video_info())
    assert opt.key == "single_video"
    assert opt.kind is MediaKind.VIDEO


def test_single_photo_picks_single_photo_option() -> None:
    opt = _provider().default_option(_single_photo_info())
    assert opt.key == "single_photo"
    assert opt.kind is MediaKind.PHOTO


def test_mixed_gallery_picks_gallery_all() -> None:
    opt = _provider().default_option(
        _gallery_info((MediaKind.VIDEO, MediaKind.PHOTO, MediaKind.VIDEO))
    )
    assert opt.key == "gallery_all"
    assert opt.kind is MediaKind.GALLERY


def test_all_video_gallery_picks_gallery_all() -> None:
    opt = _provider().default_option(_gallery_info((MediaKind.VIDEO, MediaKind.VIDEO)))
    assert opt.key == "gallery_all"


def test_all_photo_gallery_picks_gallery_all() -> None:
    opt = _provider().default_option(
        _gallery_info((MediaKind.PHOTO, MediaKind.PHOTO, MediaKind.PHOTO))
    )
    assert opt.key == "gallery_all"


def test_unknown_kind_raises() -> None:
    # Providers must raise rather than silently pick anything.
    info = MediaInfo(
        platform=Platform.INSTAGRAM,
        media_id="weird",
        title="??",
        kind=MediaKind.AUDIO,  # not expected for IG
        items=(),
        raw={"is_gallery": False, "kinds": []},
    )
    with pytest.raises(DownloadError):
        _provider().default_option(info)


def test_default_option_is_subset_of_build_options() -> None:
    info = _single_video_info()
    default = _provider().default_option(info)
    all_opts = _provider().build_options(info)
    assert any(o.key == default.key for o in all_opts)
