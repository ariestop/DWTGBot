"""Guards ``is_stale_youtube_cache`` — the helper that invalidates
legacy ``MediaCache`` rows written before YouTube provider learned to
stash ``size_by_height``. Without this, the resolution-picker buttons
would keep showing the bitrate-formula fallback (3-6x over real size)
for up to ``MEDIA_CACHE_TTL_SECONDS`` after the fix ships.
"""

from __future__ import annotations

from app.application.services.media_cache_codec import is_stale_youtube_cache
from app.domain.entities.media_info import MediaInfo
from app.domain.enums import MediaKind, Platform


def _yt(raw: dict[str, object]) -> MediaInfo:
    return MediaInfo(
        platform=Platform.YOUTUBE,
        media_id="abc",
        title="t",
        kind=MediaKind.VIDEO,
        duration_sec=60.0,
        raw=raw,
    )


def test_missing_key_counts_as_stale() -> None:
    assert is_stale_youtube_cache(_yt({"available_heights": [720, 1080]})) is True


def test_present_empty_map_is_fresh() -> None:
    # Empty dict means "provider ran with the new code but yt-dlp
    # really had no filesize" — the formula fallback is intentional
    # there, no need to blow the cache.
    assert is_stale_youtube_cache(_yt({"available_heights": [720], "size_by_height": {}})) is False


def test_populated_map_is_fresh() -> None:
    assert (
        is_stale_youtube_cache(
            _yt({"available_heights": [720], "size_by_height": {"720": 80_000_000}})
        )
        is False
    )


def test_non_youtube_platforms_never_stale() -> None:
    other = MediaInfo(
        platform=Platform.INSTAGRAM,
        media_id="x",
        title="t",
        kind=MediaKind.VIDEO,
        duration_sec=10.0,
        raw={},
    )
    assert is_stale_youtube_cache(other) is False
