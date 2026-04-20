"""``RedisNoticeThrottle`` — L4 audit fix.

Uses a tiny hand-rolled fake redis (no ``fakeredis`` in dev.txt) to
verify that the throttle:

* claims a slot (returns True) when ``SET NX EX`` succeeds,
* declines (returns False) when the key already exists,
* fails open (returns True) on ``RedisError``.
"""

from __future__ import annotations

from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.infrastructure.cache.redis_notice_throttle import RedisNoticeThrottle


class _FakeRedis:
    """Minimal async-fake: tracks `set(nx=True, ex=...)` calls.

    ``set`` returns True when the key was absent (slot claimed),
    ``None`` when the key already exists (redis-py's actual behaviour
    for the NX failure path).
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self.calls: list[tuple[str, bytes, int]] = []
        self.raise_on_set: Exception | None = None

    async def set(  # type: ignore[override]
        self,
        name: str,
        value: Any,
        *,
        nx: bool = False,
        ex: int | None = None,
        **_: Any,
    ) -> bool | None:
        if self.raise_on_set is not None:
            raise self.raise_on_set
        self.calls.append((name, value, ex or 0))
        if nx and name in self._store:
            return None
        self._store[name] = value
        return True


@pytest.mark.asyncio
async def test_claims_slot_when_key_absent() -> None:
    redis = _FakeRedis()
    throttle = RedisNoticeThrottle(redis)  # type: ignore[arg-type]

    claimed = await throttle.should_notify(scope_key="user:1", ttl_s=60)

    assert claimed is True
    assert redis.calls == [("rl:notice:user:1", b"1", 60)]


@pytest.mark.asyncio
async def test_declines_when_key_exists() -> None:
    redis = _FakeRedis()
    throttle = RedisNoticeThrottle(redis)  # type: ignore[arg-type]

    first = await throttle.should_notify(scope_key="user:1", ttl_s=60)
    second = await throttle.should_notify(scope_key="user:1", ttl_s=60)

    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_fails_open_on_redis_error() -> None:
    """Redis wobble MUST NOT silence the notice — better one extra
    reply than a silently swallowed rate-limit message."""
    redis = _FakeRedis()
    redis.raise_on_set = RedisConnectionError("boom")
    throttle = RedisNoticeThrottle(redis)  # type: ignore[arg-type]

    result = await throttle.should_notify(scope_key="user:1", ttl_s=60)

    assert result is True


@pytest.mark.asyncio
async def test_custom_key_prefix() -> None:
    redis = _FakeRedis()
    throttle = RedisNoticeThrottle(redis, key_prefix="notice:")  # type: ignore[arg-type]

    await throttle.should_notify(scope_key="chat:42", ttl_s=30)

    assert redis.calls == [("notice:chat:42", b"1", 30)]
