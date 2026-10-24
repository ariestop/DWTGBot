"""Redis-backed :class:`DeliveryLedger`.

One JSON string per job, ``delivered:{job_id}``. The TTL covers every
arq attempt of the job (``JOB_TIMEOUT_SECONDS * (JOB_MAX_RETRIES + 1)``)
plus an hour: after that no retry can come. Redis errors are logged at
warning and swallowed, like the cancellation flag store.
"""

from __future__ import annotations

import dataclasses
import json

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.application.services.delivery_ledger import DeliveredRecord, DeliveryLedger
from app.config import Settings
from app.domain.enums import DeliveryMethod
from app.logging_config import get_logger

_logger = get_logger(__name__)


def _key(job_id: int) -> str:
    return f"delivered:{job_id}"


class RedisDeliveryLedger(DeliveryLedger):
    __slots__ = ("_r", "_ttl_s")

    def __init__(self, *, redis: Redis, settings: Settings) -> None:
        self._r = redis
        self._ttl_s = settings.JOB_TIMEOUT_SECONDS * (settings.JOB_MAX_RETRIES + 1) + 3_600

    async def record(self, *, job_id: int, delivered: DeliveredRecord) -> None:
        payload = dataclasses.asdict(delivered) | {"method": delivered.method.value}
        try:
            await self._r.set(_key(job_id), json.dumps(payload), ex=self._ttl_s)
        except RedisError:
            _logger.warning("delivery_ledger_record_failed", job_id=job_id)

    async def get(self, *, job_id: int) -> DeliveredRecord | None:
        try:
            raw = await self._r.get(_key(job_id))
        except RedisError:
            _logger.warning("delivery_ledger_get_failed", job_id=job_id)
            return None
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            data["method"] = DeliveryMethod(data["method"])
            return DeliveredRecord(**data)
        except (ValueError, KeyError, TypeError):
            _logger.warning("delivery_ledger_entry_invalid", job_id=job_id)
            return None
