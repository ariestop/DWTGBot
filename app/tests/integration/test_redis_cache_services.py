"""Integration tests for Redis-backed cache services (real Redis).

These tests are opt-in like the Postgres integration suite: they run
only when ``DWTGBOT_TEST_REDIS_URL`` is present. CI wires that env var
to the ephemeral Redis service added in audit fix A25.
"""

from __future__ import annotations

import os

import pytest
from redis.asyncio import Redis

from app.infrastructure.cache.redis_post_text_store import RedisPostTextStore

pytestmark = pytest.mark.integration

_REDIS_URL = os.environ.get("DWTGBOT_TEST_REDIS_URL")
if not _REDIS_URL:
    pytest.skip(
        "DWTGBOT_TEST_REDIS_URL is not set — skipping Redis integration tests",
        allow_module_level=True,
    )


@pytest.fixture
async def redis_client():
    client = Redis.from_url(_REDIS_URL, decode_responses=False)
    try:
        await client.flushdb()
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


def _settings(ttl: int = 60):
    return type("_S", (), {"POST_TEXT_TTL_SEC": ttl})()


async def test_post_text_store_round_trips_and_sets_ttl(redis_client: Redis) -> None:
    store = RedisPostTextStore(redis=redis_client, settings=_settings())  # type: ignore[arg-type]

    await store.put(job_id=42, text="hello from redis")

    assert await store.exists(job_id=42) is True
    assert await store.get(job_id=42) == "hello from redis"
    ttl = await redis_client.ttl("post_text:42")
    assert 0 < int(ttl) <= 60
