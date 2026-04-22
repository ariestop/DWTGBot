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


def test_real_size_cascade_picks_vp9_when_no_avc1_at_height() -> None:
    """The most common YouTube layout today is H.264 only up to 720p
    and VP9/AV1 at 1080p. The size estimator must fall through to the
    VP9 filesize for the 1080p bucket instead of dropping to the
    bitrate formula (root cause of the user report).
    """
    from app.infrastructure.providers.youtube import _real_video_size_for_height

    formats = [
        {
            "height": 720,
            "vcodec": "avc1.64001f",
            "acodec": "none",
            "ext": "mp4",
            "filesize_approx": 80_000_000,
        },
        {
            "height": 1080,
            "vcodec": "vp09.00.40.08",
            "acodec": "none",
            "ext": "webm",
            "filesize_approx": 120_000_000,
        },
        {
            "height": None,
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "ext": "m4a",
            "filesize_approx": 5_000_000,
        },
    ]
    # Chain 1 (avc1+mp4): picks avc1 720p = 80 MB; + 5 MB audio = 85 MB.
    assert _real_video_size_for_height(formats, 1080) == 85_000_000
    # Without any avc1 track at all, chain 3 (mp4) is empty too --
    # chain 4 picks the vp9 1080p format.
    vp9_only = [f for f in formats if not str(f.get("vcodec") or "").startswith("avc1")]
    assert _real_video_size_for_height(vp9_only, 1080) == 125_000_000


def test_real_size_returns_none_when_no_filesize_anywhere() -> None:
    from app.infrastructure.providers.youtube import _real_video_size_for_height

    formats = [
        {"height": 720, "vcodec": "avc1", "acodec": "none", "ext": "mp4"},
        {"height": 1080, "vcodec": "vp9", "acodec": "none", "ext": "webm"},
    ]
    assert _real_video_size_for_height(formats, 1080) is None
