"""
Redis-backed :class:`PostTextStore`.

* One key per job: ``post_text:{job_id}`` → UTF-8 bytes of the
  description.
* TTL ``Settings.POST_TEXT_TTL_SEC`` (default 24h). The TTL is the
  product expiration, not a cleanup crutch — the bot alerts the user
  with "больше недоступен" when the key is gone, which is a perfectly
  acceptable end-state.
* ``decode_responses`` is client-wide; we tolerate both ``bytes`` and
  ``str`` to keep the impl drop-in across test doubles.

Errors are swallowed at the storage boundary: a Redis outage MUST NOT
abort an auto-enqueue that already succeeded at the DB + arq layer.
"""

from __future__ import annotations

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.application.services.post_text_store import PostTextStore
from app.config import Settings
from app.logging_config import get_logger

_logger = get_logger(__name__)


def _key(job_id: int) -> str:
    return f"post_text:{job_id}"


def _decode(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class RedisPostTextStore(PostTextStore):
    """Async Redis implementation."""

    __slots__ = ("_r", "_settings")

    def __init__(self, *, redis: Redis, settings: Settings) -> None:
        self._r = redis
        self._settings = settings

    async def put(self, *, job_id: int, text: str) -> None:
        if not text:
            return
        try:
            await self._r.set(
                _key(job_id),
                text,
                ex=self._settings.POST_TEXT_TTL_SEC,
            )
        except RedisError:
            # Best-effort: log and move on. ADR-0010 §2.3 — a missing
            # post-text surfaces as "больше недоступен" on the first
            # button tap, which is the same UX as a TTL expiry.
            _logger.warning("post_text_put_failed", job_id=job_id)

    async def get(self, *, job_id: int) -> str | None:
        try:
            value = await self._r.get(_key(job_id))
        except RedisError:
            _logger.warning("post_text_get_failed", job_id=job_id)
            return None
        return _decode(value)

    async def exists(self, *, job_id: int) -> bool:
        try:
            count = await self._r.exists(_key(job_id))
        except RedisError:
            _logger.warning("post_text_exists_failed", job_id=job_id)
            return False
        return bool(count)
