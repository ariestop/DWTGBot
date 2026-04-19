"""Rate-limit metrics port.

Application-layer abstraction over the actual metrics backend
(Prometheus in production, ``NoopRateLimitMetrics`` in tests and when
``METRICS_ENABLED=false``). Mirrors the names from
``docs/36-rate-limiting.md`` §8 — change them only together with the
docs (P9: docs/tests/config updated together).

The port intentionally stays tiny: every method takes plain primitives
so the application layer never imports a metrics SDK. The Prometheus
implementation lives under ``app/infrastructure/metrics/``.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.rate_limit import LimitLayer


class RateLimitMetrics(Protocol):
    """Limiter-side counters. Hot path — implementations MUST be O(1)."""

    def inc_decision(self, *, decision: str, layer: LimitLayer | None) -> None:
        """``rate_limit_decisions_total{decision, layer}``.

        ``decision`` is ``"allow"`` or ``"deny"``. ``layer`` is ``None``
        for allows (matches the §7 logging contract).
        """

    def observe_retry_after(self, *, layer: LimitLayer, seconds: int) -> None:
        """``rate_limit_retry_after_seconds{layer}`` — histogram."""

    def inc_redis_error(self, *, kind: str) -> None:
        """``rate_limit_redis_errors_total{kind}`` — kind is e.g. ``timeout``,
        ``lua_error``, ``parse``, ``unexpected``."""

    def inc_first_deny(self, *, layer: LimitLayer, user_id: int) -> None:
        """``rate_limit_first_deny_total{layer[, user_id]}``.

        Bumped only when the notice-throttle gate releases the *first*
        deny in a window (see ``docs/36-`` §4.3, §8.1). Implementations
        decide whether to attach ``user_id`` as a label — see
        ``METRICS_USER_ID_LABEL`` in ``app/config.py`` and
        ``ADR-0006`` for the cardinality trade-off.
        """


class NoopRateLimitMetrics:
    """Used when ``METRICS_ENABLED=false`` and in tests that don't care."""

    def inc_decision(self, *, decision: str, layer: LimitLayer | None) -> None:
        return

    def observe_retry_after(self, *, layer: LimitLayer, seconds: int) -> None:
        return

    def inc_redis_error(self, *, kind: str) -> None:
        return

    def inc_first_deny(self, *, layer: LimitLayer, user_id: int) -> None:
        return
