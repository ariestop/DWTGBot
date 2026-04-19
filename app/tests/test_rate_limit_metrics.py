"""Metrics sink — Noop + Prometheus + integration through ``evaluate()``.

Covers:
- ``NoopRateLimitMetrics`` is callable and returns nothing surprising
  (the application code never has to None-check).
- ``PrometheusRateLimitMetrics`` writes the right metric/label combo.
- ``evaluate()`` bumps decision counters and the retry-after histogram.
- ``RedisRateLimitGate`` bumps ``rate_limit_redis_errors_total`` on
  failures (covers §3.4 fail-open observability).
- ``METRICS_USER_ID_LABEL`` toggles cardinality.
"""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry
from prometheus_client.exposition import generate_latest
from redis.exceptions import RedisError

from app.application.services.rate_limit import evaluate
from app.application.services.rate_limit_gate import NoopRateLimitGate
from app.application.services.rate_limit_metrics import NoopRateLimitMetrics
from app.config import RateLimitWindows, parse_rl_window
from app.domain.rate_limit import LimitDecision, LimitLayer, LimitWindow
from app.infrastructure.cache.redis_rate_limit_gate import RedisRateLimitGate
from app.infrastructure.metrics.prometheus_metrics import (
    PrometheusRateLimitMetrics,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _windows() -> RateLimitWindows:
    return RateLimitWindows(
        user_burst=parse_rl_window("5/60", layer=LimitLayer.USER_BURST),
        user_hourly=parse_rl_window("20/3600", layer=LimitLayer.USER_HOURLY),
        chat_burst=parse_rl_window("15/60", layer=LimitLayer.CHAT_BURST),
        global_burst=parse_rl_window("60/60", layer=LimitLayer.GLOBAL_BURST),
        domain_burst=parse_rl_window("30/60", layer=LimitLayer.DOMAIN_BURST),
    )


def _registry_text(registry: CollectorRegistry) -> str:
    return generate_latest(registry).decode("utf-8")


class _AlwaysDenyGate:
    async def check(self, key: str, window: LimitWindow) -> LimitDecision:
        return LimitDecision.deny(layer=window.layer, retry_after_s=window.window_s)


# ---------------------------------------------------------------------------
# Noop sink
# ---------------------------------------------------------------------------


def test_noop_metrics_does_nothing() -> None:
    sink = NoopRateLimitMetrics()
    sink.inc_decision(decision="allow", layer=None)
    sink.inc_decision(decision="deny", layer=LimitLayer.USER_BURST)
    sink.observe_retry_after(layer=LimitLayer.USER_BURST, seconds=42)
    sink.inc_redis_error(kind="timeout")
    sink.inc_first_deny(layer=LimitLayer.USER_BURST, user_id=1)


# ---------------------------------------------------------------------------
# Prometheus sink — direct
# ---------------------------------------------------------------------------


class TestPrometheusSinkDirect:
    def test_records_decision_with_layer(self) -> None:
        reg = CollectorRegistry()
        sink = PrometheusRateLimitMetrics(registry=reg)

        sink.inc_decision(decision="deny", layer=LimitLayer.USER_BURST)
        sink.inc_decision(decision="allow", layer=None)

        text = _registry_text(reg)
        assert 'rate_limit_decisions_total{decision="deny",layer="user_burst"} 1.0' in text
        assert 'rate_limit_decisions_total{decision="allow",layer="none"} 1.0' in text

    def test_observes_retry_histogram(self) -> None:
        reg = CollectorRegistry()
        sink = PrometheusRateLimitMetrics(registry=reg)
        sink.observe_retry_after(layer=LimitLayer.GLOBAL_BURST, seconds=12)
        text = _registry_text(reg)
        assert "rate_limit_retry_after_seconds_count" in text
        assert 'layer="global_burst"' in text

    def test_redis_errors_by_kind(self) -> None:
        reg = CollectorRegistry()
        sink = PrometheusRateLimitMetrics(registry=reg)
        sink.inc_redis_error(kind="timeout")
        sink.inc_redis_error(kind="parse")
        sink.inc_redis_error(kind="timeout")
        text = _registry_text(reg)
        assert 'rate_limit_redis_errors_total{kind="timeout"} 2.0' in text
        assert 'rate_limit_redis_errors_total{kind="parse"} 1.0' in text

    def test_first_deny_without_user_label_default(self) -> None:
        reg = CollectorRegistry()
        sink = PrometheusRateLimitMetrics(registry=reg)
        sink.inc_first_deny(layer=LimitLayer.USER_BURST, user_id=999)
        sink.inc_first_deny(layer=LimitLayer.USER_BURST, user_id=1000)
        text = _registry_text(reg)
        # No user_id label leakage by default.
        assert 'rate_limit_first_deny_total{layer="user_burst"} 2.0' in text
        assert "user_id=" not in text

    def test_first_deny_with_user_label_when_enabled(self) -> None:
        reg = CollectorRegistry()
        sink = PrometheusRateLimitMetrics(registry=reg, user_id_label_enabled=True)
        sink.inc_first_deny(layer=LimitLayer.USER_BURST, user_id=999)
        text = _registry_text(reg)
        assert 'rate_limit_first_deny_total{layer="user_burst",user_id="999"} 1.0' in text


# ---------------------------------------------------------------------------
# evaluate() integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_records_allow_when_no_deny() -> None:
    reg = CollectorRegistry()
    sink = PrometheusRateLimitMetrics(registry=reg)
    decision = await evaluate(
        user_id=1,
        chat_id=2,
        domain="youtube.com",
        gate=NoopRateLimitGate(),
        windows=_windows(),
        metrics=sink,
    )
    assert decision.allowed is True
    text = _registry_text(reg)
    assert 'rate_limit_decisions_total{decision="allow",layer="none"} 1.0' in text
    # Histogram should NOT be touched on allow.
    assert "rate_limit_retry_after_seconds_count" not in text or (
        "rate_limit_retry_after_seconds_count{layer=" not in text
    )


@pytest.mark.asyncio
async def test_evaluate_records_deny_and_retry_histogram() -> None:
    reg = CollectorRegistry()
    sink = PrometheusRateLimitMetrics(registry=reg)
    decision = await evaluate(
        user_id=1,
        chat_id=2,
        domain="youtube.com",
        gate=_AlwaysDenyGate(),
        windows=_windows(),
        metrics=sink,
    )
    assert decision.allowed is False
    text = _registry_text(reg)
    assert 'rate_limit_decisions_total{decision="deny",layer="user_burst"} 1.0' in text
    # Histogram count for the layer increased by exactly one.
    assert 'rate_limit_retry_after_seconds_count{layer="user_burst"} 1.0' in text


@pytest.mark.asyncio
async def test_evaluate_without_metrics_argument_does_not_explode() -> None:
    """``metrics`` is optional — the bot may pass ``None`` from old call sites."""
    decision = await evaluate(
        user_id=1,
        chat_id=2,
        domain=None,
        gate=NoopRateLimitGate(),
        windows=_windows(),
    )
    assert decision.allowed is True


# ---------------------------------------------------------------------------
# Redis gate → redis_errors counter
# ---------------------------------------------------------------------------


class _ExplodingScript:
    async def __call__(self, **_kwargs: object) -> None:
        raise RedisError("dead")


class _ExplodingRedis:
    def register_script(self, _src: str) -> _ExplodingScript:
        return _ExplodingScript()


@pytest.mark.asyncio
async def test_redis_gate_increments_redis_error_counter() -> None:
    reg = CollectorRegistry()
    sink = PrometheusRateLimitMetrics(registry=reg)
    gate = RedisRateLimitGate(_ExplodingRedis(), metrics=sink)  # type: ignore[arg-type]
    window = LimitWindow(layer=LimitLayer.USER_BURST, limit=5, window_s=60)

    decision = await gate.check("rl:user:1:burst", window)

    # Fail-open keeps the user happy …
    assert decision.allowed is True
    # … but the operator sees the failure.
    text = _registry_text(reg)
    assert 'rate_limit_redis_errors_total{kind="RedisError"} 1.0' in text
