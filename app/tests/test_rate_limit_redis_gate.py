"""``RedisRateLimitGate`` — exercises every branch of the §3.4 contract.

We avoid pulling in ``fakeredis`` (not in dev.txt) by faking only the
two surface points the gate uses: ``register_script`` and the Script
object it returns. The Lua semantics live in
``test_rate_limit_evaluate.py`` against the in-memory ``_CountingGate``;
here we check that the *gate itself* maps Redis output / failure into
the right ``LimitDecision`` shape.
"""

from __future__ import annotations

from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from app.domain.rate_limit import LimitLayer, LimitWindow
from app.infrastructure.cache.redis_rate_limit_gate import RedisRateLimitGate


class _FakeScript:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[tuple[list[str], list[Any]]] = []

    async def __call__(self, *, keys: list[str], args: list[Any]) -> Any:
        self.calls.append((keys, args))
        if isinstance(self.payload, BaseException):
            raise self.payload
        if callable(self.payload):
            return self.payload(keys, args)
        return self.payload


class _FakeRedis:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.script: _FakeScript | None = None

    def register_script(self, _src: str) -> _FakeScript:
        self.script = _FakeScript(self._payload)
        return self.script


_W = LimitWindow(layer=LimitLayer.USER_BURST, limit=5, window_s=60)


@pytest.mark.asyncio
async def test_allow_when_lua_says_allow() -> None:
    gate = RedisRateLimitGate(_FakeRedis(payload=[1, 3, 45_000]))  # type: ignore[arg-type]
    d = await gate.check("rl:user:1:burst", _W)
    assert d.allowed is True
    assert d.layer is None
    assert d.retry_after_s == 0


@pytest.mark.asyncio
async def test_deny_uses_layer_and_ceil_pttl() -> None:
    # 12_300 ms → 13 seconds (ceil)
    gate = RedisRateLimitGate(_FakeRedis(payload=[0, 6, 12_300]))  # type: ignore[arg-type]
    d = await gate.check("rl:user:1:burst", _W)
    assert d.allowed is False
    assert d.layer is LimitLayer.USER_BURST
    assert d.retry_after_s == 13


@pytest.mark.asyncio
async def test_deny_falls_back_to_window_when_pttl_negative() -> None:
    # Edge case: PTTL -1 (no TTL) — must NOT propagate "0 seconds" to user.
    gate = RedisRateLimitGate(_FakeRedis(payload=[0, 6, -1]))  # type: ignore[arg-type]
    d = await gate.check("rl:user:1:burst", _W)
    assert d.allowed is False
    assert d.retry_after_s == _W.window_s


@pytest.mark.asyncio
async def test_fail_open_on_redis_error() -> None:
    gate = RedisRateLimitGate(_FakeRedis(payload=RedisConnectionError("boom")))  # type: ignore[arg-type]
    d = await gate.check("rl:user:1:burst", _W)
    assert d.allowed is True


@pytest.mark.asyncio
async def test_fail_open_on_garbage_payload() -> None:
    gate = RedisRateLimitGate(_FakeRedis(payload="not-a-list"))  # type: ignore[arg-type]
    d = await gate.check("rl:user:1:burst", _W)
    assert d.allowed is True


@pytest.mark.asyncio
async def test_fail_closed_when_configured() -> None:
    gate = RedisRateLimitGate(
        _FakeRedis(payload=RedisError("dead")),  # type: ignore[arg-type]
        fail_open=False,
    )
    d = await gate.check("rl:user:1:burst", _W)
    assert d.allowed is False
    assert d.retry_after_s == 60
