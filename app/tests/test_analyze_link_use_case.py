"""``AnalyzeLinkUseCase.execute``: provider, media cache and request state.

The cache is best-effort in both directions: a failed read or write
falls back to the provider instead of failing the user's link.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.application.dto.media import AnalyzedMedia
from app.application.services.media_cache_codec import info_to_metadata
from app.application.use_cases.analyze_link import AnalyzeLinkUseCase
from app.domain.entities.media_info import DownloadOption, MediaInfo
from app.domain.enums import MediaKind, Platform
from app.domain.repositories.media_cache_repo import MediaCacheRecord, MediaCacheRepository
from app.exceptions import InvalidUrlError

_IG_URL = "https://www.instagram.com/p/ABC123/"
_YT_URL = "https://www.youtube.com/watch?v=abc"


def _info(platform: Platform, *, raw: dict[str, Any] | None = None) -> MediaInfo:
    return MediaInfo(
        platform=platform,
        media_id="m1",
        title="Title",
        kind=MediaKind.VIDEO,
        duration_sec=10.0,
        raw=raw or {},
    )


class _FakeProvider:
    def __init__(self, info: MediaInfo) -> None:
        self.info = info
        self.get_info_calls: list[str] = []

    async def get_info(self, url: str) -> MediaInfo:
        self.get_info_calls.append(url)
        return self.info

    def build_options(self, info: MediaInfo) -> list[DownloadOption]:
        return [DownloadOption(key="video", label="Видео", kind=MediaKind.VIDEO)]


class _FakeRegistry:
    def __init__(self, provider: _FakeProvider) -> None:
        self.provider = provider

    def get(self, platform: Platform) -> _FakeProvider:
        return self.provider


class _FakeState:
    def __init__(self) -> None:
        self.saved: dict[str, tuple[AnalyzedMedia, int]] = {}

    async def save(self, request_id: str, payload: AnalyzedMedia, ttl_seconds: int) -> None:
        self.saved[request_id] = (payload, ttl_seconds)

    async def load(self, request_id: str) -> AnalyzedMedia | None:  # pragma: no cover
        return None

    async def delete(self, request_id: str) -> None:  # pragma: no cover
        return None


class _FakeCache(MediaCacheRepository):
    def __init__(
        self,
        record: MediaCacheRecord | None = None,
        *,
        read_error: Exception | None = None,
        write_error: Exception | None = None,
    ) -> None:
        self.record = record
        self.read_error = read_error
        self.write_error = write_error
        self.reads: list[str] = []
        self.upserts: list[MediaCacheRecord] = []

    async def get_fresh(self, source_url: str) -> MediaCacheRecord | None:
        self.reads.append(source_url)
        if self.read_error is not None:
            raise self.read_error
        return self.record

    async def upsert(self, record: MediaCacheRecord) -> MediaCacheRecord:
        if self.write_error is not None:
            raise self.write_error
        self.upserts.append(record)
        return record

    async def purge_expired(self) -> int:  # pragma: no cover
        return 0


def _record(info: MediaInfo, url: str) -> MediaCacheRecord:
    now = datetime.now(UTC)
    return MediaCacheRecord(
        id=1,
        source_url=url,
        platform=info.platform,
        media_id=info.media_id,
        title=info.title,
        metadata_json=info_to_metadata(info),
        expires_at=now + timedelta(minutes=10),
        created_at=now,
        updated_at=now,
    )


def _use_case(
    provider: _FakeProvider, *, cache: _FakeCache | None = None
) -> tuple[AnalyzeLinkUseCase, _FakeState]:
    state = _FakeState()
    use_case = AnalyzeLinkUseCase(
        providers=_FakeRegistry(provider),  # type: ignore[arg-type]
        state_store=state,
        request_ttl_seconds=600,
        media_cache=cache,
    )
    return use_case, state


async def test_miss_asks_provider_writes_cache_and_saves_state() -> None:
    provider = _FakeProvider(_info(Platform.INSTAGRAM))
    cache = _FakeCache()
    use_case, state = _use_case(provider, cache=cache)

    out = await use_case.execute(f"глянь {_IG_URL}")

    assert provider.get_info_calls == [cache.reads[0]]
    assert [r.source_url for r in cache.upserts] == cache.reads
    assert [o.key for o in out.analyzed.options] == ["video"]
    assert state.saved[out.request_id] == (out.analyzed, 600)


async def test_fresh_cache_hit_skips_provider() -> None:
    provider = _FakeProvider(_info(Platform.INSTAGRAM))
    cache = _FakeCache(_record(_info(Platform.INSTAGRAM), _IG_URL))
    use_case, state = _use_case(provider, cache=cache)

    out = await use_case.execute(_IG_URL)

    assert provider.get_info_calls == []
    assert cache.upserts == []
    assert out.analyzed.info.title == "Title"
    assert out.request_id in state.saved


async def test_legacy_youtube_cache_entry_is_refetched() -> None:
    # Written before the provider stored ``size_by_height``.
    legacy = _info(Platform.YOUTUBE)
    provider = _FakeProvider(_info(Platform.YOUTUBE, raw={"size_by_height": {"720": 1}}))
    cache = _FakeCache(_record(legacy, _YT_URL))
    use_case, _ = _use_case(provider, cache=cache)

    await use_case.execute(_YT_URL)

    assert len(provider.get_info_calls) == 1
    assert len(cache.upserts) == 1


async def test_cache_read_failure_falls_back_to_provider() -> None:
    provider = _FakeProvider(_info(Platform.INSTAGRAM))
    cache = _FakeCache(read_error=ConnectionError("db down"))
    use_case, state = _use_case(provider, cache=cache)

    out = await use_case.execute(_IG_URL)

    assert len(provider.get_info_calls) == 1
    assert out.request_id in state.saved


async def test_cache_write_failure_still_returns_analysis() -> None:
    provider = _FakeProvider(_info(Platform.INSTAGRAM))
    cache = _FakeCache(write_error=ConnectionError("db down"))
    use_case, state = _use_case(provider, cache=cache)

    out = await use_case.execute(_IG_URL)

    assert out.request_id in state.saved


async def test_without_cache_provider_is_the_only_source() -> None:
    provider = _FakeProvider(_info(Platform.INSTAGRAM))
    use_case, _ = _use_case(provider)

    await use_case.execute(_IG_URL)

    assert len(provider.get_info_calls) == 1


async def test_text_without_url_is_rejected_before_any_io() -> None:
    provider = _FakeProvider(_info(Platform.INSTAGRAM))
    cache = _FakeCache()
    use_case, state = _use_case(provider, cache=cache)

    with pytest.raises(InvalidUrlError):
        await use_case.execute("просто текст")

    assert cache.reads == []
    assert provider.get_info_calls == []
    assert state.saved == {}
