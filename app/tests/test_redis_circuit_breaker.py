"""``RedisCircuitBreaker`` — L5 audit fix.

State transitions under a fake Redis (minimal surface: ``get``,
``incr``, ``expire``, ``set``, ``delete``).
"""

from __future__ import annotations

from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.infrastructure.cache.redis_circuit_breaker import RedisCircuitBreaker


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.raise_on: str | None = None

    def _maybe_raise(self, op: str) -> None:
        if self.raise_on == op:
            raise RedisConnectionError(f"boom-on-{op}")

    async def get(self, key: str) -> bytes | None:
        self._maybe_raise("get")
        return self.store.get(key)

    async def incr(self, key: str) -> int:
        self._maybe_raise("incr")
        val = int(self.store.get(key, b"0")) + 1
        self.store[key] = str(val).encode()
        return val

    async def expire(self, key: str, seconds: int) -> bool:
        self._maybe_raise("expire")
        del seconds
        return key in self.store

    async def set(
        self, key: str, value: Any, *, ex: int | None = None, nx: bool = False, **_: Any
    ) -> bool | None:
        self._maybe_raise("set")
        if nx and key in self.store:
            return None
        self.store[key] = value
        del ex
        return True

    async def delete(self, *keys: str) -> int:
        self._maybe_raise("delete")
        removed = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                removed += 1
        return removed


def _make(redis: _FakeRedis, *, threshold: int = 3) -> RedisCircuitBreaker:
    return RedisCircuitBreaker(
        redis,  # type: ignore[arg-type]
        failure_threshold=threshold,
        window_seconds=60,
        cooldown_seconds=300,
    )


@pytest.mark.asyncio
async def test_initial_state_is_closed() -> None:
    redis = _FakeRedis()
    cb = _make(redis)
    assert await cb.is_open("youtube.com") is False


@pytest.mark.asyncio
async def test_opens_after_threshold_failures() -> None:
    redis = _FakeRedis()
    cb = _make(redis, threshold=3)

    assert await cb.record_failure("youtube.com") == 1
    assert await cb.is_open("youtube.com") is False
    assert await cb.record_failure("youtube.com") == 2
    assert await cb.is_open("youtube.com") is False
    assert await cb.record_failure("youtube.com") == 3

    # At/above threshold: breaker opens.
    assert await cb.is_open("youtube.com") is True


@pytest.mark.asyncio
async def test_success_resets_failure_counter() -> None:
    redis = _FakeRedis()
    cb = _make(redis, threshold=3)

    await cb.record_failure("youtube.com")
    await cb.record_failure("youtube.com")
    assert "cb:youtube.com:failures" in redis.store

    await cb.record_success("youtube.com")
    assert "cb:youtube.com:failures" not in redis.store

    # A fresh failure starts from 1 again.
    assert await cb.record_failure("youtube.com") == 1


@pytest.mark.asyncio
async def test_hosts_are_independent() -> None:
    redis = _FakeRedis()
    cb = _make(redis, threshold=2)

    await cb.record_failure("youtube.com")
    await cb.record_failure("youtube.com")

    assert await cb.is_open("youtube.com") is True
    assert await cb.is_open("instagram.com") is False


@pytest.mark.asyncio
async def test_empty_host_is_noop() -> None:
    """Blank hostnames disable the breaker for the call — we can't
    key on the empty string without causing false positives."""
    redis = _FakeRedis()
    cb = _make(redis)

    assert await cb.is_open("") is False
    assert await cb.record_failure("") == 0
    await cb.record_success("")
    assert redis.store == {}


@pytest.mark.asyncio
async def test_fails_open_on_redis_error() -> None:
    """Redis unreachable must not itself open the breaker — that would
    compound an outage into a self-inflicted denial of service."""
    redis = _FakeRedis()
    redis.raise_on = "get"
    cb = _make(redis)

    assert await cb.is_open("youtube.com") is False


@pytest.mark.asyncio
async def test_incr_error_returns_zero() -> None:
    redis = _FakeRedis()
    redis.raise_on = "incr"
    cb = _make(redis)

    assert await cb.record_failure("youtube.com") == 0
