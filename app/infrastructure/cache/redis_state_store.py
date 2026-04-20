"""
Redis-backed RequestStateStore.

Stores AnalyzedMedia under a short request_id with an explicit TTL.

Serialization format (S4 audit fix)
-----------------------------------
The previous implementation used :mod:`pickle`. Even though values
never leave the trust boundary, pickle is a footgun:

  * Any compromise of Redis (or a misconfigured ACL) escalates to
    arbitrary code execution on the bot/worker process — pickle
    happily reconstructs callables.
  * Cross-version dataclass changes silently corrupt rollouts: a
    field renamed in MediaInfo will deserialize into a stale
    instance, and the bug only surfaces hours later when callbacks
    fire on stale state.

We now serialize through plain JSON with an explicit ``v1:``-prefixed
envelope. JSON is bandit-clean, parseable by humans (operators can
``redis-cli GET req:xyz`` and read the payload), and avoids pulling in
a wheel-less native dependency on cp314. The first 3 bytes are a
versioned magic header (``v1:``) so we can change the schema later
without parsing ambiguity. Unknown / future versions are treated as a
cache miss rather than an error: the user simply re-sends the link.

Backward compatibility: any in-flight pickle entries written by the
previous code expire within ``MEDIA_CACHE_TTL_SECONDS`` (6 h by
default). During the transition we treat them as cache misses
instead of unpickling — strictly safer than honouring legacy data.
"""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from app.application.dto.media import AnalyzedMedia
from app.application.services.request_state_store import RequestStateStore
from app.domain.entities.media_info import (
    DownloadOption,
    MediaInfo,
    MediaItem,
)
from app.domain.enums import MediaKind, Platform
from app.logging_config import get_logger

_KEY_PREFIX = "req:"
# Versioned envelope. Bump when AnalyzedMedia / nested dataclasses
# change in an incompatible way; the load path below short-circuits
# unknown versions to ``None`` so callers degrade to "re-analyse".
_SCHEMA_VERSION = b"v1:"

_logger = get_logger(__name__)


class RedisRequestStateStore(RequestStateStore):
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    @staticmethod
    def _key(request_id: str) -> str:
        return f"{_KEY_PREFIX}{request_id}"

    async def save(self, request_id: str, payload: AnalyzedMedia, ttl_seconds: int) -> None:
        body = json.dumps(_to_dict(payload), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        encoded = _SCHEMA_VERSION + body
        await self._r.set(self._key(request_id), encoded, ex=ttl_seconds)

    async def load(self, request_id: str) -> AnalyzedMedia | None:
        data = await self._r.get(self._key(request_id))
        if data is None:
            return None
        if not isinstance(data, bytes):
            # redis-py returns bytes by default; defensive narrowing
            # keeps mypy happy and protects against decode_responses=True
            # being flipped on the shared connection later.
            return None
        if not data.startswith(_SCHEMA_VERSION):
            # Either legacy pickle bytes (transition window) or a
            # future schema. Treat as a miss; the bot will re-analyse.
            _logger.info("request_state_unknown_envelope", request_id=request_id)
            return None
        try:
            payload = json.loads(data[len(_SCHEMA_VERSION) :].decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            _logger.warning("request_state_decode_failed", request_id=request_id)
            return None
        try:
            return _from_dict(payload)
        except (KeyError, TypeError, ValueError):
            _logger.warning("request_state_shape_mismatch", request_id=request_id)
            return None

    async def delete(self, request_id: str) -> None:
        await self._r.delete(self._key(request_id))


# --------------------------- (de)serialization ---------------------------
#
# We serialise to plain dicts/lists so the wire format is inspectable
# (``redis-cli GET req:...`` returns readable JSON) and stable across
# Python versions. Enums are stored as their string ``value``; tuples
# become lists and are restored to tuples on load to preserve the
# ``frozen=True, slots=True`` dataclass contract.


def _to_dict(media: AnalyzedMedia) -> dict[str, Any]:
    return {
        "info": _info_to_dict(media.info),
        "options": [_option_to_dict(o) for o in media.options],
    }


def _info_to_dict(info: MediaInfo) -> dict[str, Any]:
    return {
        "platform": info.platform.value,
        "media_id": info.media_id,
        "title": info.title,
        "kind": info.kind.value,
        "duration_sec": info.duration_sec,
        "items": [_item_to_dict(i) for i in info.items],
        "thumbnail_url": info.thumbnail_url,
        "raw": info.raw,
    }


def _item_to_dict(item: MediaItem) -> dict[str, Any]:
    return {
        "kind": item.kind.value,
        "url": item.url,
        "width": item.width,
        "height": item.height,
        "duration_sec": item.duration_sec,
    }


def _option_to_dict(opt: DownloadOption) -> dict[str, Any]:
    return {
        "key": opt.key,
        "label": opt.label,
        "kind": opt.kind.value,
        "height": opt.height,
        "bitrate_kbps": opt.bitrate_kbps,
        "container": opt.container,
        "estimated_size_bytes": opt.estimated_size_bytes,
    }


def _from_dict(data: dict[str, Any]) -> AnalyzedMedia:
    return AnalyzedMedia(
        info=_info_from_dict(data["info"]),
        options=tuple(_option_from_dict(o) for o in data["options"]),
    )


def _info_from_dict(data: dict[str, Any]) -> MediaInfo:
    return MediaInfo(
        platform=Platform(data["platform"]),
        media_id=data["media_id"],
        title=data["title"],
        kind=MediaKind(data["kind"]),
        duration_sec=data.get("duration_sec"),
        items=tuple(_item_from_dict(i) for i in data.get("items", [])),
        thumbnail_url=data.get("thumbnail_url"),
        raw=dict(data.get("raw") or {}),
    )


def _item_from_dict(data: dict[str, Any]) -> MediaItem:
    return MediaItem(
        kind=MediaKind(data["kind"]),
        url=data["url"],
        width=data.get("width"),
        height=data.get("height"),
        duration_sec=data.get("duration_sec"),
    )


def _option_from_dict(data: dict[str, Any]) -> DownloadOption:
    return DownloadOption(
        key=data["key"],
        label=data["label"],
        kind=MediaKind(data["kind"]),
        height=data.get("height"),
        bitrate_kbps=data.get("bitrate_kbps"),
        container=data.get("container"),
        estimated_size_bytes=data.get("estimated_size_bytes"),
    )
