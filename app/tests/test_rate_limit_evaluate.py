"""``evaluate()`` walks the layers in §2.1 order and short-circuits on first deny.

These tests use a fake counter map instead of Redis — that's the whole
point of the ``RateLimitGate`` port (docs/36- §5.1).
"""

from __future__ import annotations

import pytest

from app.application.services.rate_limit import InMemoryNoticeThrottle, evaluate
from app.application.services.rate_limit_gate import NoopRateLimitGate
from app.config import RateLimitWindows, parse_rl_window
from app.domain.rate_limit import LimitDecision, LimitLayer, LimitWindow


def _windows(
    *,
    user_burst: str = "5/60",
    user_hourly: str = "20/3600",
    chat_burst: str = "15/60",
    global_burst: str = "60/60",
    domain_burst: str = "30/60",
) -> RateLimitWindows:
    return RateLimitWindows(
        user_burst=parse_rl_window(user_burst, layer=LimitLayer.USER_BURST),
        user_hourly=parse_rl_window(user_hourly, layer=LimitLayer.USER_HOURLY),
        chat_burst=parse_rl_window(chat_burst, layer=LimitLayer.CHAT_BURST),
        global_burst=parse_rl_window(global_burst, layer=LimitLayer.GLOBAL_BURST),
        domain_burst=parse_rl_window(domain_burst, layer=LimitLayer.DOMAIN_BURST),
    )


class _CountingGate:
    """In-memory leaky bucket. Mirrors the Lua semantics for tests."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.calls: list[tuple[str, str]] = []  # (key, layer)

    async def check(self, key: str, window: LimitWindow) -> LimitDecision:
        self.calls.append((key, window.layer.value))
        n = self.counters.get(key, 0) + 1
        self.counters[key] = n
        if n > window.limit:
            return LimitDecision.deny(layer=window.layer, retry_after_s=window.window_s)
        return LimitDecision.allow()


class _AlwaysDenyGate:
    def __init__(self, only_layer: LimitLayer | None = None) -> None:
        self._only = only_layer

    async def check(self, key: str, window: LimitWindow) -> LimitDecision:
        if self._only is None or window.layer is self._only:
            return LimitDecision.deny(layer=window.layer, retry_after_s=window.window_s)
        return LimitDecision.allow()


@pytest.mark.asyncio
async def test_noop_gate_always_allows() -> None:
    decision = await evaluate(
        user_id=1,
        chat_id=2,
        domain="youtube.com",
        gate=NoopRateLimitGate(),
        windows=_windows(),
    )
    assert decision.allowed is True
    assert decision.layer is None
    assert decision.retry_after_s == 0


@pytest.mark.asyncio
async def test_layers_walked_in_documented_order() -> None:
    gate = _CountingGate()
    await evaluate(
        user_id=10,
        chat_id=20,
        domain="youtube.com",
        gate=gate,
        windows=_windows(),
    )
    layers_called = [layer for _, layer in gate.calls]
    assert layers_called == [
        LimitLayer.USER_BURST.value,
        LimitLayer.USER_HOURLY.value,
        LimitLayer.CHAT_BURST.value,
        LimitLayer.GLOBAL_BURST.value,
        LimitLayer.DOMAIN_BURST.value,
    ]


@pytest.mark.asyncio
async def test_short_circuits_on_first_deny() -> None:
    gate = _AlwaysDenyGate(only_layer=LimitLayer.USER_BURST)
    decision = await evaluate(
        user_id=1,
        chat_id=2,
        domain="youtube.com",
        gate=gate,
        windows=_windows(),
    )
    assert decision.allowed is False
    assert decision.layer is LimitLayer.USER_BURST


@pytest.mark.asyncio
async def test_skips_domain_layer_when_domain_unknown() -> None:
    gate = _CountingGate()
    decision = await evaluate(
        user_id=1,
        chat_id=2,
        domain=None,
        gate=gate,
        windows=_windows(),
    )
    assert decision.allowed is True
    layers_called = [layer for _, layer in gate.calls]
    assert LimitLayer.DOMAIN_BURST.value not in layers_called


@pytest.mark.asyncio
async def test_user_burst_trips_after_limit() -> None:
    """A real bucket: 5 allows, 6th denies on USER_BURST."""
    gate = _CountingGate()
    w = _windows(user_burst="5/60")
    for _ in range(5):
        d = await evaluate(user_id=1, chat_id=2, domain="youtube.com", gate=gate, windows=w)
        assert d.allowed
    d = await evaluate(user_id=1, chat_id=2, domain="youtube.com", gate=gate, windows=w)
    assert d.allowed is False
    assert d.layer is LimitLayer.USER_BURST
    assert d.retry_after_s >= 1


# --- NoticeThrottle ---------------------------------------------------------


@pytest.mark.asyncio
async def test_notice_throttle_first_call_speaks() -> None:
    t = InMemoryNoticeThrottle()
    assert await t.should_notify(scope_key="u:1", ttl_s=30) is True


@pytest.mark.asyncio
async def test_notice_throttle_within_ttl_stays_silent() -> None:
    t = InMemoryNoticeThrottle()
    assert await t.should_notify(scope_key="u:1", ttl_s=30) is True
    assert await t.should_notify(scope_key="u:1", ttl_s=30) is False


@pytest.mark.asyncio
async def test_notice_throttle_separate_users_independent() -> None:
    t = InMemoryNoticeThrottle()
    assert await t.should_notify(scope_key="u:1", ttl_s=30) is True
    assert await t.should_notify(scope_key="u:2", ttl_s=30) is True
