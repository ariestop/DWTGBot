"""Rate-limit evaluation service (bot edge).

Pure orchestration: walks the configured layers in the order defined by
``docs/36-rate-limiting.md`` §2 and returns the *first* denying decision
or ``LimitDecision.allow()`` if every layer passes.

This module is intentionally tiny and infra-free — the actual increment
lives behind :class:`RateLimitGate`. That makes it trivial to unit test
with the in-memory ``NoopRateLimitGate`` (or a fake counter map) and
keeps the layered policy in a single readable place.

The accompanying notice-throttling helper lives here too because
``RL_NOTICE_TTL`` is a property of *the limiter*, not of any single
handler — a future ``/cancel`` flow would reuse it.
"""

from __future__ import annotations

from app.application.services.rate_limit_gate import RateLimitGate
from app.application.services.rate_limit_metrics import (
    NoopRateLimitMetrics,
    RateLimitMetrics,
)
from app.config import RateLimitWindows
from app.domain.rate_limit import LimitDecision, LimitLayer
from app.logging_config import get_logger

_logger = get_logger(__name__)


def _domain_label_safe(domain: str | None) -> str:
    """Logs use ``"-"`` for an unknown domain so the field stays present."""
    return domain or "-"


async def evaluate(
    *,
    user_id: int,
    chat_id: int,
    domain: str | None,
    gate: RateLimitGate,
    windows: RateLimitWindows,
    metrics: RateLimitMetrics | None = None,
    request_id: str | None = None,
) -> LimitDecision:
    """Walk every layer in §2.1 order. Return the first deny, or allow.

    ``domain=None`` is allowed (e.g. message had no parseable URL) — the
    L4 check is simply skipped. We log every decision per §7's contract
    and (when ``metrics`` is provided) bump the §8 counters.
    """
    sink: RateLimitMetrics = metrics if metrics is not None else NoopRateLimitMetrics()
    checks: list[tuple[LimitLayer, str, int, int]] = [
        (
            LimitLayer.USER_BURST,
            f"rl:user:{user_id}:burst",
            windows.user_burst.limit,
            windows.user_burst.window_s,
        ),
        (
            LimitLayer.USER_HOURLY,
            f"rl:user:{user_id}:hourly",
            windows.user_hourly.limit,
            windows.user_hourly.window_s,
        ),
        (
            LimitLayer.CHAT_BURST,
            f"rl:chat:{chat_id}:burst",
            windows.chat_burst.limit,
            windows.chat_burst.window_s,
        ),
        (
            LimitLayer.GLOBAL_BURST,
            "rl:global:burst",
            windows.global_burst.limit,
            windows.global_burst.window_s,
        ),
    ]
    if domain:
        checks.append(
            (
                LimitLayer.DOMAIN_BURST,
                f"rl:domain:{domain}:burst",
                windows.domain_burst.limit,
                windows.domain_burst.window_s,
            )
        )

    for layer, key, _limit, _window_s in checks:
        # Build the LimitWindow from the per-layer tuple so the gate sees
        # exactly what the docs promise (no stale values).
        from app.domain.rate_limit import LimitWindow

        decision = await gate.check(
            key,
            LimitWindow(layer=layer, limit=_limit, window_s=_window_s),
        )
        if not decision.allowed:
            sink.inc_decision(decision="deny", layer=layer)
            sink.observe_retry_after(layer=layer, seconds=decision.retry_after_s)
            _logger.info(
                "rate_limit_decision",
                decision="deny",
                layer=layer.value,
                user_id=user_id,
                chat_id=chat_id,
                domain=_domain_label_safe(domain),
                retry_after_s=decision.retry_after_s,
                request_id=request_id,
            )
            return decision

    sink.inc_decision(decision="allow", layer=None)
    _logger.info(
        "rate_limit_decision",
        decision="allow",
        layer=None,
        user_id=user_id,
        chat_id=chat_id,
        domain=_domain_label_safe(domain),
        retry_after_s=0,
        request_id=request_id,
    )
    return LimitDecision.allow()


# ---------------------------------------------------------------------------
# Notice throttling (§4.3)
# ---------------------------------------------------------------------------


class NoticeThrottle:
    """Tracks "we already told this user" to avoid Telegram-side spam.

    Not stored in Postgres — purely transient. A Redis-backed
    implementation just needs ``SET NX EX`` on ``rl:notice:{user_id}``
    and a ``check_and_set`` returning ``True`` if the caller should
    actually send the reply. The in-memory fallback below is fine for
    single-process bot deployments and for tests.
    """

    async def should_notify(self, *, scope_key: str, ttl_s: int) -> bool:
        """Return True if we should send a reply now; False to stay silent."""
        raise NotImplementedError


class InMemoryNoticeThrottle(NoticeThrottle):
    """Single-process throttle. Acceptable: the bot is one process today."""

    def __init__(self) -> None:
        self._seen_at: dict[str, float] = {}

    async def should_notify(self, *, scope_key: str, ttl_s: int) -> bool:
        import time

        now = time.monotonic()
        last = self._seen_at.get(scope_key)
        if last is not None and (now - last) < ttl_s:
            return False
        self._seen_at[scope_key] = now
        return True
