"""Integration tests for Redis-backed cache services (real Redis).

These tests are opt-in like the Postgres integration suite: they run
only when ``DWTGBOT_TEST_REDIS_URL`` is present. CI wires that env var
to the ephemeral Redis service added in audit fix A25.
"""

from __future__ import annotations

import os

import pytest
from redis.asyncio import Redis

from app.application.services.delivery_ledger import DeliveredRecord
from app.domain.enums import DeliveryMethod
from app.infrastructure.cache.redis_delivery_ledger import RedisDeliveryLedger
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


async def test_delivery_ledger_round_trips_with_a_ttl_over_every_attempt(
    redis_client: Redis,
) -> None:
    settings = type("_S", (), {"JOB_TIMEOUT_SECONDS": 1800, "JOB_MAX_RETRIES": 2})()
    ledger = RedisDeliveryLedger(redis=redis_client, settings=settings)  # type: ignore[arg-type]
    delivered = DeliveredRecord(
        method=DeliveryMethod.TEMP_LINK,
        public_url="https://media.example/d/token",
        primary_telegram_file_id=None,
        delivered_path="/var/lib/dwtgbot/storage/jobs/9/clip.mp4",
        file_size=123,
        mime_type="video/mp4",
        title="Clip",
        media_id="abc",
        selected_format="mp4",
    )

    assert await ledger.get(job_id=9) is None
    await ledger.record(job_id=9, delivered=delivered)

    assert await ledger.get(job_id=9) == delivered
    ttl = int(await redis_client.ttl("delivered:9"))
    assert 1800 * 3 < ttl <= 1800 * 3 + 3600


async def test_delivery_ledger_treats_a_corrupt_entry_as_missing(redis_client: Redis) -> None:
    settings = type("_S", (), {"JOB_TIMEOUT_SECONDS": 60, "JOB_MAX_RETRIES": 0})()
    ledger = RedisDeliveryLedger(redis=redis_client, settings=settings)  # type: ignore[arg-type]
    await redis_client.set("delivered:9", b"not json")

    assert await ledger.get(job_id=9) is None
