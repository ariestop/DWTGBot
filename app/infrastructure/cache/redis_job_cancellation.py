"""
Redis-backed :class:`JobCancellationStore`.

Stores a single string key ``cancel:{job_id}`` with value ``1`` and TTL
``Settings.CANCEL_FLAG_TTL_SEC``. The exact value is unimportant — we
only ever check existence.

Design notes
------------
* Deliberately simple: a string + GET + SETEX is enough. HASH would
  buy nothing; we never attach metadata.
* Best-effort error handling: a Redis outage must not prevent the user
  from seeing the cancel button do *something* (the bot-side reporter
  terminal event still fires), and must not prevent the worker from
  continuing a download (flag read failure → treat as "not cancelled",
  job finishes normally; user gets the final video anyway). We log at
  warning level so operators see flag-store flakiness without
  drowning the signal channel.
"""

from __future__ import annotations

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.application.services.job_cancellation import JobCancellationStore
from app.config import Settings
from app.logging_config import get_logger

_logger = get_logger(__name__)


def _key(job_id: int) -> str:
    return f"cancel:{job_id}"


class RedisJobCancellationStore(JobCancellationStore):
    """Async Redis implementation (used in bot + worker)."""

    __slots__ = ("_r", "_settings")

    def __init__(self, *, redis: Redis, settings: Settings) -> None:
        self._r = redis
        self._settings = settings

    async def request(self, *, job_id: int) -> None:
        try:
            await self._r.set(
                _key(job_id),
                "1",
                ex=self._settings.CANCEL_FLAG_TTL_SEC,
            )
        except RedisError:
            _logger.warning("cancel_flag_set_failed", job_id=job_id)

    async def is_cancelled(self, *, job_id: int) -> bool:
        try:
            value = await self._r.get(_key(job_id))
        except RedisError:
            _logger.warning("cancel_flag_get_failed", job_id=job_id)
            return False
        return value is not None

    async def clear(self, *, job_id: int) -> None:
        try:
            await self._r.delete(_key(job_id))
        except RedisError:
            _logger.warning("cancel_flag_clear_failed", job_id=job_id)
