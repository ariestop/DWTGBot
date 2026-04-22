"""
``MediaInfo.description`` — domain field + Redis round-trip.

The field is new in ADR-0010 §2.3. These tests pin two invariants
that later PRs depend on:

1. ``description`` has a ``""`` default — providers that do not fill
   it must still construct ``MediaInfo`` without mentioning the field.
   This keeps PR 2 (providers) a strict superset of the current
   behaviour.

2. ``RedisRequestStateStore`` serialises and deserialises
   ``description`` faithfully (empty and non-empty) and tolerates
   legacy ``v1:`` envelopes written before PR 1 — where the
   ``description`` key is absent. Missing key → ``""``, never a
   deserialisation error.

The state-store tests use an in-memory fake-redis (same pattern as
``test_redis_notice_throttle.py``), so no network or ``fakeredis``
wheel is required.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from app.application.dto.media import AnalyzedMedia
from app.domain.entities.media_info import DownloadOption, MediaInfo
from app.domain.enums import MediaKind, Platform
from app.infrastructure.cache.redis_state_store import (
    RedisRequestStateStore,
)


def _make_info(*, description: str = "") -> MediaInfo:
    return MediaInfo(
        platform=Platform.YOUTUBE,
        media_id="abc",
        title="Sample",
        kind=MediaKind.VIDEO,
        duration_sec=42.0,
        description=description,
    )


def test_default_description_is_empty_string() -> None:
    info = MediaInfo(
        platform=Platform.INSTAGRAM,
        media_id="xyz",
        title="t",
        kind=MediaKind.PHOTO,
    )
    assert info.description == ""


def test_description_is_frozen() -> None:
    info = _make_info(description="hello")
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.description = "mutated"  # type: ignore[misc]


# ---------------------- Redis round-trip ----------------------


class _FakeRedis:
    """Minimal async-fake: only ``get`` / ``set`` / ``delete`` are used
    by ``RedisRequestStateStore``. Matches redis-py's return conventions
    (bytes on read, ``None`` on miss)."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def set(
        self,
        name: str,
        value: Any,
        *,
        ex: int | None = None,
        **_: Any,
    ) -> bool:
        self._store[name] = value if isinstance(value, bytes) else bytes(value)
        return True

    async def get(self, name: str) -> bytes | None:
        return self._store.get(name)

    async def delete(self, name: str) -> int:
        return 1 if self._store.pop(name, None) is not None else 0


async def test_roundtrip_preserves_non_empty_description() -> None:
    redis = _FakeRedis()
    store = RedisRequestStateStore(redis)  # type: ignore[arg-type]

    original = AnalyzedMedia(
        info=_make_info(description="Hello, мир! " * 5),
        options=(
            DownloadOption(
                key="video_720",
                label="720p",
                kind=MediaKind.VIDEO,
                height=720,
            ),
        ),
    )
    await store.save("req-1", original, ttl_seconds=60)
    loaded = await store.load("req-1")

    assert loaded is not None
    assert loaded.info.description == original.info.description


async def test_roundtrip_preserves_empty_description() -> None:
    redis = _FakeRedis()
    store = RedisRequestStateStore(redis)  # type: ignore[arg-type]

    original = AnalyzedMedia(
        info=_make_info(description=""),
        options=(),
    )
    await store.save("req-2", original, ttl_seconds=60)
    loaded = await store.load("req-2")

    assert loaded is not None
    assert loaded.info.description == ""


async def test_legacy_v1_envelope_without_description_defaults_to_empty() -> None:
    """Old payloads written before ADR-0010 are a strict subset of the
    new schema — the ``description`` key is simply absent. Loading
    such a payload must succeed and produce ``description=""``."""
    redis = _FakeRedis()
    store = RedisRequestStateStore(redis)  # type: ignore[arg-type]

    legacy_payload = {
        "info": {
            "platform": "youtube",
            "media_id": "abc",
            "title": "Legacy",
            "kind": "video",
            "duration_sec": 10.0,
            "items": [],
            "thumbnail_url": None,
            "raw": {},
        },
        "options": [],
    }
    body = json.dumps(legacy_payload, ensure_ascii=False).encode("utf-8")
    await redis.set("req:legacy", b"v1:" + body, ex=60)

    loaded = await store.load("legacy")

    assert loaded is not None
    assert loaded.info.description == ""
    assert loaded.info.title == "Legacy"
