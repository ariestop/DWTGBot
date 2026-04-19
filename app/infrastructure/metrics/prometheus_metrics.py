"""Prometheus implementation of ``RateLimitMetrics``.

All metric names mirror ``docs/36-rate-limiting.md`` §8 verbatim. If you
rename anything here, update the docs and the dashboard skeleton in
``docs/35-metrics-and-slo.md`` §8 in the same change (P9).
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

from app.domain.rate_limit import LimitLayer

# Bucket choice: rate-limit retry-after is bounded by the largest window
# (``RL_USER_HOURLY=20/3600`` → max ~3600 s). Buckets cluster around the
# user-perceptible band (1 s — 5 min) where dashboard tuning happens.
_RETRY_BUCKETS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600)


class PrometheusRateLimitMetrics:
    """Concrete metrics sink. One registry per process — passed in so the
    same instance can also register non-rate-limit metrics later (jobs,
    queue depth, etc — see docs/35- §7).
    """

    def __init__(
        self,
        *,
        registry: CollectorRegistry,
        user_id_label_enabled: bool = False,
    ) -> None:
        self._user_id_label_enabled = user_id_label_enabled

        self._decisions = Counter(
            "rate_limit_decisions_total",
            "Rate-limit decisions, partitioned by allow/deny and the deciding layer.",
            labelnames=("decision", "layer"),
            registry=registry,
        )
        self._retry_after = Histogram(
            "rate_limit_retry_after_seconds",
            "How long denied callers were asked to wait, by layer.",
            labelnames=("layer",),
            buckets=_RETRY_BUCKETS,
            registry=registry,
        )
        self._redis_errors = Counter(
            "rate_limit_redis_errors_total",
            "Redis-side failures inside the limiter (gate fail-open path).",
            labelnames=("kind",),
            registry=registry,
        )
        first_deny_labels: tuple[str, ...] = ("layer",)
        if user_id_label_enabled:
            first_deny_labels = ("layer", "user_id")
        self._first_deny = Counter(
            "rate_limit_first_deny_total",
            "First deny per user/window (excludes follow-up silent denies).",
            labelnames=first_deny_labels,
            registry=registry,
        )

    # ------------------------------------------------------------------
    # RateLimitMetrics
    # ------------------------------------------------------------------

    def inc_decision(self, *, decision: str, layer: LimitLayer | None) -> None:
        self._decisions.labels(
            decision=decision,
            layer=layer.value if layer is not None else "none",
        ).inc()

    def observe_retry_after(self, *, layer: LimitLayer, seconds: int) -> None:
        self._retry_after.labels(layer=layer.value).observe(seconds)

    def inc_redis_error(self, *, kind: str) -> None:
        self._redis_errors.labels(kind=kind).inc()

    def inc_first_deny(self, *, layer: LimitLayer, user_id: int) -> None:
        if self._user_id_label_enabled:
            self._first_deny.labels(layer=layer.value, user_id=str(user_id)).inc()
        else:
            self._first_deny.labels(layer=layer.value).inc()
