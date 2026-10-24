"""Redis-backed :class:`ProgressFeed`: what ``ProgressUpdater`` reads.

Moved out of ``app/bot/services/progress_updater.py`` (audit R15) so the
bot layer no longer talks to Redis directly. Decodes hash replies in
either ``decode_responses`` mode and maps ``RedisError`` / ``OSError``
to :class:`ProgressFeedError`.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.application.ports.progress_channel import (
    EVENTS_CHANNEL,
    PROGRESS_META_KEY_PREFIX,
    ProgressFeed,
    ProgressFeedError,
    ProgressSubscription,
    progress_key,
    progress_meta_key,
)
from app.logging_config import get_logger

_logger = get_logger(__name__)


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _decode_hash(mapping: dict[Any, Any]) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for key, value in (mapping or {}).items():
        key_text, value_text = _text(key), _text(value)
        if key_text is not None and value_text is not None:
            decoded[key_text] = value_text
    return decoded


class RedisProgressSubscription(ProgressSubscription):
    __slots__ = ("_pubsub",)

    def __init__(self, pubsub: Any) -> None:
        self._pubsub = pubsub

    async def next_job_id(self, *, wait_s: float) -> int | None:
        try:
            message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=wait_s)
        except TimeoutError:
            return None
        except (RedisError, OSError) as exc:
            raise ProgressFeedError(str(exc)) from exc
        if message is None or message.get("type") != "message":
            return None
        try:
            return int(_text(message.get("data")) or "")
        except ValueError:
            return None

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._pubsub.unsubscribe(EVENTS_CHANNEL)
        with contextlib.suppress(Exception):
            await self._pubsub.close()


class RedisProgressFeed(ProgressFeed):
    __slots__ = ("_r",)

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def read_progress(self, job_id: int) -> dict[str, str]:
        return await self._read_hash(progress_key(job_id))

    async def read_meta(self, job_id: int) -> dict[str, str]:
        return await self._read_hash(progress_meta_key(job_id))

    async def placeholders(self) -> AsyncIterator[tuple[int, dict[str, str]]]:
        try:
            async for key in self._r.scan_iter(match=f"{PROGRESS_META_KEY_PREFIX}*", count=100):
                key_text = _text(key)
                if key_text is None:
                    continue
                try:
                    job_id = int(key_text.split(":", 1)[1])
                except (IndexError, ValueError):
                    continue
                try:
                    meta = await self._read_hash(key_text)
                except ProgressFeedError:
                    _logger.warning("progress_recovery_read_failed", key=key_text)
                    continue
                if meta:
                    yield job_id, meta
        except (RedisError, OSError) as exc:
            raise ProgressFeedError(str(exc)) from exc

    async def subscribe(self) -> ProgressSubscription:
        pubsub = self._r.pubsub()
        try:
            await pubsub.subscribe(EVENTS_CHANNEL)
        except (RedisError, OSError) as exc:
            with contextlib.suppress(Exception):
                await pubsub.close()
            raise ProgressFeedError(str(exc)) from exc
        return RedisProgressSubscription(pubsub)

    async def _read_hash(self, key: str) -> dict[str, str]:
        try:
            return _decode_hash(await self._r.hgetall(key))
        except (RedisError, OSError) as exc:
            raise ProgressFeedError(str(exc)) from exc
