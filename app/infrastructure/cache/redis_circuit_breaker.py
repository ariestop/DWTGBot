"""Redis-backed per-host circuit breaker (L5 audit fix).

Every worker in the fleet shares the same breaker state via two
Redis keys per upstream host:

* ``cb:{host}:failures`` — count of consecutive *throttle-shaped*
  failures within a rolling window. Reset to zero on the first
  successful call to ``host``.
* ``cb:{host}:open_until`` — set to a short-lived sentinel when
  the failure count crosses the threshold. While the key exists
  every call to ``host`` is rejected fast with
  :class:`UpstreamUnavailableError` (retryable, so arq reschedules
  the job — by then the cooldown will have expired).

We deliberately use *two* keys rather than a half-open "probe"
state machine:

* Two keys is what a typical 429 storm needs. Any worker that
  sees the breaker closed will probe upstream on its own; the
  global cooldown handles the aggregate backpressure.
* A half-open probe would demand a single-probe lock, which
  complicates the code for little gain on a < 10-worker fleet.

The module is intentionally independent of ``yt-dlp`` — it
operates on arbitrary host strings so other upstream callers
(a future ``InstagramGraphAPI`` client, for example) can reuse it.
Fail-open on Redis errors mirrors the RL gate: we'd rather make
one extra upstream call than reject a legitimate request.
"""

from __future__ import annotations

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.logging_config import get_logger

_logger = get_logger(__name__)


class RedisCircuitBreaker:
    def __init__(
        self,
        redis: Redis,
        *,
        failure_threshold: int,
        window_seconds: int,
        cooldown_seconds: int,
        key_prefix: str = "cb:",
    ) -> None:
        self._redis = redis
        self._threshold = failure_threshold
        self._window_s = window_seconds
        self._cooldown_s = cooldown_seconds
        self._prefix = key_prefix

    def _open_key(self, host: str) -> str:
        return f"{self._prefix}{host}:open_until"

    def _failures_key(self, host: str) -> str:
        return f"{self._prefix}{host}:failures"

    async def is_open(self, host: str) -> bool:
        """True iff the breaker for ``host`` is currently open."""
        if not host:
            return False
        try:
            val = await self._redis.get(self._open_key(host))
        except RedisError as exc:
            _logger.warning(
                "circuit_breaker_redis_error",
                kind=type(exc).__name__,
                op="is_open",
                host=host,
            )
            return False  # fail-open
        return val is not None

    async def record_success(self, host: str) -> None:
        """Reset the consecutive-failure counter for ``host``."""
        if not host:
            return
        try:
            await self._redis.delete(self._failures_key(host))
        except RedisError as exc:  # pragma: no cover  best-effort
            _logger.warning(
                "circuit_breaker_redis_error",
                kind=type(exc).__name__,
                op="record_success",
                host=host,
            )

    async def record_failure(self, host: str) -> int:
        """Bump the failure counter for ``host`` and, if the threshold
        is reached, open the breaker for ``cooldown_seconds``.

        Returns the new failure count (0 if Redis was unreachable —
        we fail-open on the bookkeeping path, too).
        """
        if not host:
            return 0
        failures_key = self._failures_key(host)
        try:
            count = int(await self._redis.incr(failures_key))
            if count == 1:
                # Only set TTL on the first increment — subsequent
                # increments within the window inherit the original
                # expiry. Otherwise a steady stream of failures would
                # keep pushing the reset horizon out forever.
                await self._redis.expire(failures_key, self._window_s)
        except RedisError as exc:
            _logger.warning(
                "circuit_breaker_redis_error",
                kind=type(exc).__name__,
                op="record_failure",
                host=host,
            )
            return 0

        if count >= self._threshold:
            try:
                # ``nx=True`` prevents racing workers from each
                # refreshing the TTL in a second window. The first
                # one to cross the threshold wins; the rest see
                # ``is_open() == True`` and skip upstream.
                await self._redis.set(
                    self._open_key(host),
                    b"1",
                    ex=self._cooldown_s,
                    nx=True,
                )
                _logger.warning(
                    "circuit_breaker_opened",
                    host=host,
                    failures=count,
                    cooldown_s=self._cooldown_s,
                )
            except RedisError as exc:  # pragma: no cover  best-effort
                _logger.warning(
                    "circuit_breaker_redis_error",
                    kind=type(exc).__name__,
                    op="open",
                    host=host,
                )
        return count
