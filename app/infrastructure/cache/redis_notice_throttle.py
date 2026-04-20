"""Redis-backed notice throttle (L4 audit fix).

Replaces the single-process ``InMemoryNoticeThrottle`` with a
``SET NX EX`` primitive so two bot replicas behind the same Telegram
webhook don't both send "you're over the rate limit" to the same
user back-to-back.

Semantics are simple enough to fit in one round-trip: ``SET key 1
NX EX ttl``.

* Returns ``True`` (we *should* notify) **iff** the command
  reported the key was absent before the write. This is the
  moment we're claiming the notification slot, so the mark is
  left in place for ``ttl`` seconds.
* Returns ``False`` if the key already existed — another request
  has already claimed the slot within the window.

Fail-open is mandatory (P11 parity with ``RedisRateLimitGate``):
on transport / reply errors we return ``True`` and log
``notice_throttle_redis_error``. The worst case is one extra
reply to the user, which is strictly better than going silent
when Redis wobbles.
"""

from __future__ import annotations

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.application.services.rate_limit import NoticeThrottle
from app.logging_config import get_logger

_logger = get_logger(__name__)


class RedisNoticeThrottle(NoticeThrottle):
    """Production notice throttle. Uses ``SET NX EX`` per scope_key."""

    def __init__(self, redis: Redis, *, key_prefix: str = "rl:notice:") -> None:
        self._redis = redis
        self._key_prefix = key_prefix

    async def should_notify(self, *, scope_key: str, ttl_s: int) -> bool:
        key = f"{self._key_prefix}{scope_key}"
        try:
            # ``set(..., nx=True)`` returns True when the key was absent
            # (i.e. we claimed the slot), None otherwise. redis-py's
            # return type is ``bool | None``; ``is True`` distinguishes
            # the two without the ambiguity of truthiness on ``None``.
            claimed = await self._redis.set(key, b"1", nx=True, ex=ttl_s)
        except RedisError as exc:
            _logger.warning(
                "notice_throttle_redis_error",
                kind=type(exc).__name__,
                scope_key=scope_key,
            )
            # Fail-open: permit the notice. Better a duplicate reply
            # than silent dropping under a Redis wobble.
            return True
        except Exception as exc:  # pragma: no cover  defensive
            _logger.exception(
                "notice_throttle_unexpected_error",
                kind=type(exc).__name__,
                scope_key=scope_key,
            )
            return True
        return claimed is True
