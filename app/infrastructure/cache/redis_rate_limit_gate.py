"""Redis-backed rate-limit gate (leaky token bucket).

Implements :class:`app.application.services.rate_limit_gate.RateLimitGate`
using a single Lua script for atomic ``INCR + EXPIRE`` so a check costs
one Redis round-trip per layer (see ``docs/36-rate-limiting.md`` §3).

Fail-open policy is mandatory (P11): if Redis raises, times out, or the
script returns garbage, we return ``LimitDecision.allow()`` and log
``rate_limit_redis_error``. Setting ``RL_FAIL_OPEN=false`` flips this
behaviour, and Settings.validate_runtime requires an ADR-grade reason
to do so in production.
"""

from __future__ import annotations

import math
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.application.services.rate_limit_gate import RateLimitGate
from app.application.services.rate_limit_metrics import (
    NoopRateLimitMetrics,
    RateLimitMetrics,
)
from app.domain.rate_limit import LimitDecision, LimitWindow
from app.logging_config import get_logger

_logger = get_logger(__name__)

# KEYS[1] — the rate-limit counter key.
# ARGV[1] — limit (integer).
# ARGV[2] — window TTL in seconds (integer).
# Returns: { allowed (1|0), current_count, pttl_ms }
_LUA_INCR_AND_CHECK = """
local current = tonumber(redis.call('INCR', KEYS[1]))
if current == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
local pttl = redis.call('PTTL', KEYS[1])
if current > tonumber(ARGV[1]) then
  return {0, current, pttl}
end
return {1, current, pttl}
"""


class RedisRateLimitGate(RateLimitGate):
    """Production gate. Uses a Lua script registered once per process."""

    def __init__(
        self,
        redis: Redis,
        *,
        fail_open: bool = True,
        metrics: RateLimitMetrics | None = None,
    ) -> None:
        self._redis = redis
        self._fail_open = fail_open
        self._metrics: RateLimitMetrics = metrics if metrics is not None else NoopRateLimitMetrics()
        # ``register_script`` returns a Script object. ``EVALSHA`` is used
        # transparently; the body is auto-loaded on NOSCRIPT.
        self._script = redis.register_script(_LUA_INCR_AND_CHECK)

    async def check(self, key: str, window: LimitWindow) -> LimitDecision:
        try:
            raw: Any = await self._script(
                keys=[key],
                args=[window.limit, window.window_s],
            )
        except RedisError as exc:
            kind = type(exc).__name__
            _logger.warning(
                "rate_limit_redis_error",
                kind=kind,
                layer=window.layer.value,
            )
            self._metrics.inc_redis_error(kind=kind)
            return self._on_failure()
        except Exception as exc:  # pragma: no cover  defensive
            _logger.exception(
                "rate_limit_unexpected_error",
                kind=type(exc).__name__,
                layer=window.layer.value,
            )
            self._metrics.inc_redis_error(kind="unexpected")
            return self._on_failure()

        try:
            allowed_int, _current, pttl_ms = raw
            allowed = int(allowed_int) == 1
            pttl = int(pttl_ms)
        except (TypeError, ValueError, IndexError):
            _logger.exception(
                "rate_limit_lua_parse_error",
                layer=window.layer.value,
                raw=str(raw)[:64],
            )
            self._metrics.inc_redis_error(kind="parse")
            return self._on_failure()

        if allowed:
            return LimitDecision.allow()

        # PTTL can be -1 (no TTL — shouldn't happen since we set it on
        # first INCR) or -2 (key gone). Default to the configured window
        # so the user never sees "0 seconds" or a negative number.
        retry_ms = pttl if pttl > 0 else window.window_s * 1000
        retry_after_s = max(1, math.ceil(retry_ms / 1000))
        return LimitDecision.deny(layer=window.layer, retry_after_s=retry_after_s)

    def _on_failure(self) -> LimitDecision:
        if self._fail_open:
            return LimitDecision.allow()
        # Fail-closed: we still cannot synthesise a meaningful retry-after
        # without the counter, so use the longest sensible window (60 s).
        # See docs/36- §3.4 — flipping this requires an ADR.
        from app.domain.rate_limit import LimitLayer

        return LimitDecision.deny(layer=LimitLayer.GLOBAL_BURST, retry_after_s=60)
