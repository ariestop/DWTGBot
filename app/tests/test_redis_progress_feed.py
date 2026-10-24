"""``RedisProgressFeed``: decoding and error mapping (audit R15).

``ProgressUpdater`` logic is covered in test_progress_updater.py, which
runs it over this feed and a fake Redis.
"""

from __future__ import annotations

from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.application.ports.progress_channel import ProgressFeedError
from app.infrastructure.cache.redis_progress_feed import (
    RedisProgressFeed,
    RedisProgressSubscription,
)


class _DownRedis:
    async def hgetall(self, _key: str) -> dict[bytes, bytes]:
        raise RedisConnectionError("down")

    def scan_iter(self, **_: Any) -> Any:
        raise RedisConnectionError("down")


class _BytesRedis:
    async def hgetall(self, _key: str) -> dict[bytes, bytes]:
        return {b"stage": b"downloading", b"percent": b"42.0"}


async def test_hash_is_decoded_to_str() -> None:
    feed = RedisProgressFeed(_BytesRedis())  # type: ignore[arg-type]

    assert await feed.read_progress(1) == {"stage": "downloading", "percent": "42.0"}


async def test_redis_errors_become_feed_errors() -> None:
    feed = RedisProgressFeed(_DownRedis())  # type: ignore[arg-type]

    with pytest.raises(ProgressFeedError):
        await feed.read_meta(1)
    with pytest.raises(ProgressFeedError):
        async for _ in feed.placeholders():
            pass


class _PubSub:
    def __init__(self, messages: list[Any]) -> None:
        self._messages = messages

    async def get_message(self, **_: Any) -> Any:
        item = self._messages.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"type": "message", "data": b"17"}, 17),
        ({"type": "message", "data": "18"}, 18),
        ({"type": "message", "data": b"not-a-job"}, None),
        ({"type": "subscribe", "data": 1}, None),
        (None, None),
        (TimeoutError(), None),
    ],
)
async def test_subscription_yields_job_ids(message: Any, expected: int | None) -> None:
    sub = RedisProgressSubscription(_PubSub([message]))

    assert await sub.next_job_id(wait_s=0.1) == expected


async def test_subscription_errors_become_feed_errors() -> None:
    sub = RedisProgressSubscription(_PubSub([RedisConnectionError("reset")]))

    with pytest.raises(ProgressFeedError):
        await sub.next_job_id(wait_s=0.1)
