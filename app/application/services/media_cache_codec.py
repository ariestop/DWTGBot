"""``MediaInfo`` ⇄ ``media_cache.metadata_json`` round-trip.

Shared by the bot (``AnalyzeLinkUseCase``) and the worker
(``ProcessDownloadUseCase``) so both read the same cache row the same way.
"""

from __future__ import annotations

from app.domain.entities.media_info import MediaInfo, MediaItem
from app.domain.enums import MediaKind, Platform
from app.domain.repositories.media_cache_repo import MediaCacheRecord


def info_to_metadata(info: MediaInfo) -> dict[str, object]:
    """Persist a *full* MediaInfo round-trip into ``metadata_json``.

    ``raw`` is provider-specific (yt-dlp gives back nested JSON) but is
    a precondition for ``build_options`` (e.g. YouTube reads
    ``available_heights`` from it). We serialise it as-is — Postgres
    JSONB handles arbitrary nested primitives. ``items`` is flattened
    to a list-of-dicts so we can rebuild the tuple on load without
    relying on dataclass internals.
    """
    return {
        "kind": info.kind.value,
        "duration_sec": info.duration_sec,
        "thumbnail_url": info.thumbnail_url,
        "items": [
            {
                "kind": i.kind.value,
                "url": i.url,
                "width": i.width,
                "height": i.height,
                "duration_sec": i.duration_sec,
            }
            for i in info.items
        ],
        "raw": info.raw,
    }


def info_from_cache(record: MediaCacheRecord, *, platform: Platform) -> MediaInfo:
    """Rebuild a ``MediaInfo`` from a cache row.

    Mirrors ``info_to_metadata``. Defensive on missing keys so an
    older cache row written before the schema settled still yields a
    usable (degraded) ``MediaInfo`` instead of a 500 to the user.
    """
    metadata = record.metadata_json or {}
    items_raw = metadata.get("items") or []
    items = tuple(
        MediaItem(
            kind=MediaKind(it["kind"]),
            url=str(it.get("url") or ""),
            width=it.get("width"),
            height=it.get("height"),
            duration_sec=it.get("duration_sec"),
        )
        for it in items_raw
        if isinstance(it, dict) and it.get("kind")
    )
    return MediaInfo(
        platform=platform,
        media_id=record.media_id or "",
        title=record.title or "",
        kind=MediaKind(metadata.get("kind") or MediaKind.VIDEO.value),
        duration_sec=metadata.get("duration_sec"),
        items=items,
        thumbnail_url=metadata.get("thumbnail_url"),
        raw=dict(metadata.get("raw") or {}) | {"source_url": record.source_url, "cached": True},
    )


def is_stale_youtube_cache(info: MediaInfo) -> bool:
    """Return True if a cache row predates the ``size_by_height`` fix.

    We look for the key explicitly: empty dicts are acceptable (happens
    for sources where yt-dlp really published no filesize — button
    falls back to the bitrate formula on purpose). ``missing``, on the
    other hand, is the unambiguous shape of entries written before the
    provider started populating this field.
    """
    if info.platform is not Platform.YOUTUBE:
        return False
    return "size_by_height" not in (info.raw or {})
