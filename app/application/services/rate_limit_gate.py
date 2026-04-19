"""Rate-limit gate port.

Application-layer abstraction over the actual storage backend
(Redis in production, in-memory ``NoopRateLimitGate`` in tests). Kept
infra-free so ``app/application/services/rate_limit.py`` can be unit
tested without touching Redis.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.rate_limit import LimitDecision, LimitWindow


class RateLimitGate(Protocol):
    """Atomic ``increment-and-check`` against an opaque counter store.

    Contract:
    - ``key`` uniquely identifies a (layer, subject) pair (e.g.
      ``rl:user:42:burst``). Implementations MUST NOT inspect or mutate
      the key beyond using it as the counter identifier.
    - On first increment of a key, the implementation MUST set TTL =
      ``window.window_s`` so counters expire automatically.
    - On infrastructure failure (Redis down, Lua error, …) the
      implementation MUST honour the fail-open policy: return
      ``LimitDecision.allow()`` and bump its own error metric. The
      caller does not retry. See ``docs/36-rate-limiting.md`` §3.4.
    """

    async def check(self, key: str, window: LimitWindow) -> LimitDecision: ...


class NoopRateLimitGate:
    """Always-allow gate. Used when ``RL_ENABLED=false`` and in tests."""

    async def check(self, key: str, window: LimitWindow) -> LimitDecision:
        return LimitDecision.allow()
