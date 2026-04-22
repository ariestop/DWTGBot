"""YouTube provider — option-building logic (no network)."""

from __future__ import annotations

from app.config import get_settings
from app.domain.entities.media_info import MediaInfo
from app.domain.enums import MediaKind, Platform
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


def test_only_real_heights_offered() -> None:
    opts = _provider().build_options(_info([240, 360, 480]))
    keys = {o.key for o in opts}
    assert "video_360" in keys
    assert "video_480" in keys
    assert "video_720" not in keys
    assert "video_1080" not in keys
    assert "audio_mp3" in keys


def test_audio_always_offered_even_without_video() -> None:
    opts = _provider().build_options(_info(heights=[]))
    keys = {o.key for o in opts}
    assert keys == {"audio_mp3"}


def test_buckets_show_when_higher_source_available() -> None:
    opts = _provider().build_options(_info([1080]))
    keys = {o.key for o in opts}
    # Higher max source → all four buckets are reachable via downscaling.
    assert {"video_360", "video_480", "video_720", "video_1080"}.issubset(keys)


def test_estimated_size_set_when_duration_known() -> None:
    opts = _provider().build_options(_info([720], duration=120.0))
    video_720 = next(o for o in opts if o.key == "video_720")
    assert video_720.estimated_size_bytes is not None and video_720.estimated_size_bytes > 0


def test_no_estimated_size_when_duration_unknown() -> None:
    opts = _provider().build_options(_info([720], duration=None))
    audio = next(o for o in opts if o.key == "audio_mp3")
    assert audio.estimated_size_bytes is None


def _info_with_real_sizes(size_by_height: dict[str, int]) -> MediaInfo:
    # 1080p duration x 5000 kbps formula would give ~750 MB; we want the
    # real value to clearly beat the formula so assertions below cannot
    # accidentally pass on fallback math.
    return MediaInfo(
        platform=Platform.YOUTUBE,
        media_id="abc",
        title="Test",
        kind=MediaKind.VIDEO,
        duration_sec=1200.0,
        raw={
            "available_heights": [720, 1080],
            "duration": 1200.0,
            "size_by_height": size_by_height,
        },
    )


def test_estimated_size_prefers_real_filesize_over_formula() -> None:
    # Real 1080p clip: 95 MB; formula would report ~750 MB for the same
    # duration. Verify the button reflects the real figure, which is the
    # original user complaint.
    real_1080 = 95 * 1024 * 1024
    opts = _provider().build_options(_info_with_real_sizes({"1080": real_1080}))
    video_1080 = next(o for o in opts if o.key == "video_1080")
    assert video_1080.estimated_size_bytes == real_1080


def test_estimated_size_falls_back_to_formula_when_filesize_missing() -> None:
    # 720p has no real size → formula kicks in; 1080p does → uses real.
    opts = _provider().build_options(_info_with_real_sizes({"1080": 50_000_000}))
    video_720 = next(o for o in opts if o.key == "video_720")
    video_1080 = next(o for o in opts if o.key == "video_1080")
    assert video_720.estimated_size_bytes is not None and video_720.estimated_size_bytes > 0
    assert video_1080.estimated_size_bytes == 50_000_000
