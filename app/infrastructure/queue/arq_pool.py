"""arq Redis pool helpers."""

from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import Settings

# Single source of truth for the arq queue name. Both ends MUST agree:
# - the producer's pool (``build_arq_pool``) sets ``default_queue_name``
#   here so ``enqueue_job`` lands on the same zset the worker reads;
# - ``WorkerSettings.queue_name`` imports this constant.
# Discovered when wiring the queue-depth sampler for ``ADR-0007`` —
# without this constant the bot was enqueuing onto arq's library
# default ``"arq:queue"`` while the worker was listening on
# ``"arq:dwtgbot"`` (silent drift). The Gauge would have been
# permanently zero and SLO B3 unmeasurable.
WORKER_QUEUE_NAME = "arq:dwtgbot"


def build_redis_settings(settings: Settings) -> RedisSettings:
    return RedisSettings(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        database=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
        conn_timeout=10,
    )


async def build_arq_pool(settings: Settings) -> ArqRedis:
    return await create_pool(
        build_redis_settings(settings),
        default_queue_name=WORKER_QUEUE_NAME,
    )
