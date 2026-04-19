"""
Redis-backed RequestStateStore.

Stores AnalyzedMedia under a short request_id with an explicit TTL.
Serialized as pickle — values never leave the trust boundary (server stores
its own values), and pickle is the cheapest option that survives the
nested dataclasses inside MediaInfo. If you want to harden further, swap
to msgpack with explicit dumpers per type.
"""

from __future__ import annotations

import pickle  # noqa: S403  trusted in/out, see module docstring

from redis.asyncio import Redis

from app.application.dto.media import AnalyzedMedia
from app.application.services.request_state_store import RequestStateStore

_KEY_PREFIX = "req:"


class RedisRequestStateStore(RequestStateStore):
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    @staticmethod
    def _key(request_id: str) -> str:
        return f"{_KEY_PREFIX}{request_id}"

    async def save(self, request_id: str, payload: AnalyzedMedia, ttl_seconds: int) -> None:
        await self._r.set(self._key(request_id), pickle.dumps(payload), ex=ttl_seconds)

    async def load(self, request_id: str) -> AnalyzedMedia | None:
        data = await self._r.get(self._key(request_id))
        if data is None:
            return None
        return pickle.loads(data)  # noqa: S301  trusted

    async def delete(self, request_id: str) -> None:
        await self._r.delete(self._key(request_id))
