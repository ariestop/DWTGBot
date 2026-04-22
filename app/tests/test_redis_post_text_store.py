"""Redis-backed :class:`PostTextStore` — ADR-0010 §2.3.

Same pattern as ``test_media_info_description``: hand-rolled minimal
async fake instead of pulling in ``fakeredis``. We only exercise the
three methods the port defines, plus the "Redis outage" degrade path
that matters for the button-visibility contract.
"""

from __future__ import annotations

import pytest
from redis.exceptions import RedisError

from app.infrastructure.cache.redis_post_text_store import RedisPostTextStore


class _FakeRedis:
    """In-memory stand-in for redis-py's async client."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[bytes, int | None]] = {}
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.set_calls.append((key, value, ex))
        self._data[key] = (value.encode("utf-8"), ex)

    async def get(self, key: str) -> bytes | None:
        entry = self._data.get(key)
        return entry[0] if entry else None

    async def exists(self, key: str) -> int:
        return 1 if key in self._data else 0


class _BoomRedis(_FakeRedis):
    async def set(self, *args, **kwargs):  # type: ignore[override]
        raise RedisError("boom")

    async def get(self, *args, **kwargs):  # type: ignore[override]
        raise RedisError("boom")

    async def exists(self, *args, **kwargs):  # type: ignore[override]
        raise RedisError("boom")


def _settings(ttl: int = 86_400):
    return type("_S", (), {"POST_TEXT_TTL_SEC": ttl})()


@pytest.mark.asyncio
async def test_put_writes_under_namespaced_key_with_ttl() -> None:
    redis = _FakeRedis()
    store = RedisPostTextStore(redis=redis, settings=_settings())  # type: ignore[arg-type]

    await store.put(job_id=42, text="hello")

    assert redis.set_calls == [("post_text:42", "hello", 86_400)]


@pytest.mark.asyncio
async def test_put_skips_empty_text() -> None:
    # Auto-enqueue is supposed to filter short descriptions, but the
    # store still defends against empty writes — a ``SET key ""`` with
    # a 24h TTL would look like a present-but-empty post, sending the
    # user to a blank reply.
    redis = _FakeRedis()
    store = RedisPostTextStore(redis=redis, settings=_settings())  # type: ignore[arg-type]

    await store.put(job_id=42, text="")

    assert redis.set_calls == []


@pytest.mark.asyncio
async def test_get_returns_decoded_string() -> None:
    redis = _FakeRedis()
    store = RedisPostTextStore(redis=redis, settings=_settings())  # type: ignore[arg-type]
    await store.put(job_id=42, text="Привет")

    value = await store.get(job_id=42)

    assert value == "Привет"


@pytest.mark.asyncio
async def test_get_returns_none_on_miss() -> None:
    redis = _FakeRedis()
    store = RedisPostTextStore(redis=redis, settings=_settings())  # type: ignore[arg-type]

    assert await store.get(job_id=99) is None


@pytest.mark.asyncio
async def test_exists_tracks_put() -> None:
    redis = _FakeRedis()
    store = RedisPostTextStore(redis=redis, settings=_settings())  # type: ignore[arg-type]

    assert await store.exists(job_id=42) is False
    await store.put(job_id=42, text="hi there, long enough")
    assert await store.exists(job_id=42) is True


@pytest.mark.asyncio
async def test_put_swallows_redis_error() -> None:
    # A failed write must not abort the caller — the auto-enqueue use
    # case already has a try/except, but the port-level fallback is
    # the canonical degrade path (button won't render, alert on tap).
    redis = _BoomRedis()
    store = RedisPostTextStore(redis=redis, settings=_settings())  # type: ignore[arg-type]

    await store.put(job_id=42, text="hi")  # must not raise


@pytest.mark.asyncio
async def test_get_swallows_redis_error_and_returns_none() -> None:
    redis = _BoomRedis()
    store = RedisPostTextStore(redis=redis, settings=_settings())  # type: ignore[arg-type]

    assert await store.get(job_id=42) is None


@pytest.mark.asyncio
async def test_exists_swallows_redis_error_and_returns_false() -> None:
    redis = _BoomRedis()
    store = RedisPostTextStore(redis=redis, settings=_settings())  # type: ignore[arg-type]

    assert await store.exists(job_id=42) is False
