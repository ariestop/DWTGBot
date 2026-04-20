"""
Analyze a user-provided URL.

Pipeline:
  1. Detect platform (validation, fast).
  2. Resolve provider via registry.
  3. Fetch metadata (provider does network IO).
  4. Build the list of user-selectable download options.
  5. Stash the result under a short request_id so the callback handler can
     resolve it back later.

Returns a request_id that the bot embeds into inline button callback_data.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.application.dto.media import AnalyzedMedia
from app.application.services.providers import ProviderRegistry
from app.application.services.request_state_store import RequestStateStore
from app.domain.entities.media_info import MediaInfo, MediaItem
from app.domain.enums import MediaKind, Platform
from app.domain.repositories.media_cache_repo import (
    MediaCacheRecord,
    MediaCacheRepository,
)
from app.logging_config import get_logger
from app.utils.url import detect

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AnalyzeLinkOutput:
    request_id: str
    analyzed: AnalyzedMedia


class AnalyzeLinkUseCase:
    """Analyse a URL → metadata + options.

    S7 (audit fix): when a ``MediaCacheRepository`` is provided we
    short-circuit the provider call for fresh URLs. The cache key is
    the *normalized* URL (so the same video opened with/without
    ``?si=`` shares an entry). Misses still hit the provider and write
    back; cache entries respect ``request_ttl_seconds`` so a stale
    title/duration cannot outlive the user-facing freshness window.
    """

    def __init__(
        self,
        *,
        providers: ProviderRegistry,
        state_store: RequestStateStore,
        request_ttl_seconds: int,
        media_cache: MediaCacheRepository | None = None,
    ) -> None:
        self._providers = providers
        self._state = state_store
        self._ttl = request_ttl_seconds
        self._cache = media_cache

    async def execute(self, raw_input: str) -> AnalyzeLinkOutput:
        detected = detect(raw_input)
        provider = self._providers.get(detected.platform)

        info: MediaInfo | None = None
        served_from_cache = False
        if self._cache is not None:
            cached = await self._cache.get_fresh(detected.normalized)
            if cached is not None:
                info = _info_from_cache(cached, platform=detected.platform)
                served_from_cache = True

        if info is None:
            info = await provider.get_info(detected.normalized)
            if self._cache is not None:
                await self._upsert_cache(detected.normalized, info)

        options = provider.build_options(info)

        if not options:
            _logger.warning(
                "no_options_built", platform=detected.platform.value, url=detected.normalized
            )

        analyzed = AnalyzedMedia(info=info, options=tuple(options))
        request_id = secrets.token_urlsafe(8)
        await self._state.save(request_id, analyzed, self._ttl)
        _logger.info(
            "link_analyzed",
            request_id=request_id,
            platform=detected.platform.value,
            options_count=len(options),
            kind=info.kind.value,
            cache_hit=served_from_cache,
        )
        return AnalyzeLinkOutput(request_id=request_id, analyzed=analyzed)

    async def _upsert_cache(self, source_url: str, info: MediaInfo) -> None:
        """Best-effort write-back. Cache failures must NOT fail the user."""
        assert self._cache is not None
        now = datetime.now(UTC)
        record = MediaCacheRecord(
            id=None,
            source_url=source_url,
            platform=info.platform,
            media_id=info.media_id or None,
            title=info.title,
            metadata_json=_info_to_metadata(info),
            expires_at=now + timedelta(seconds=self._ttl),
            created_at=now,
            updated_at=now,
        )
        try:
            await self._cache.upsert(record)
        except Exception:  # pragma: no cover  best-effort cache write
            _logger.warning("media_cache_upsert_failed", source_url=source_url)


def _info_to_metadata(info: MediaInfo) -> dict[str, object]:
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


def _info_from_cache(record: MediaCacheRecord, *, platform: Platform) -> MediaInfo:
    """Rebuild a ``MediaInfo`` from a cache row.

    Mirrors ``_info_to_metadata``. Defensive on missing keys so an
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
