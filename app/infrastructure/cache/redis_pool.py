"""Async Redis client factory."""

from __future__ import annotations

from redis.asyncio import Redis

from app.config import Settings


def build_redis(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        health_check_interval=30,
        socket_keepalive=True,
        retry_on_timeout=True,
    )
