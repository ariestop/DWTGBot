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
from app.application.services.media_cache_codec import (
    info_from_cache,
    info_to_metadata,
    is_stale_youtube_cache,
)
from app.application.services.providers import ProviderRegistry
from app.application.services.request_state_store import RequestStateStore
from app.domain.entities.media_info import MediaInfo
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
                candidate = info_from_cache(cached, platform=detected.platform)
                # Legacy entries written before YouTube provider learned
                # to stash real per-height filesizes would poison the
                # button estimate with the bitrate-formula fallback for
                # up to ``MEDIA_CACHE_TTL_SECONDS`` after the fix lands.
                # Force a refetch rather than serving stale, misleading
                # numbers — cost is one extra yt-dlp call per URL once
                # until the new entry repopulates.
                if is_stale_youtube_cache(candidate):
                    _logger.info(
                        "media_cache_legacy_miss",
                        platform=detected.platform.value,
                        url=detected.normalized,
                    )
                else:
                    info = candidate
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
            metadata_json=info_to_metadata(info),
            expires_at=now + timedelta(seconds=self._ttl),
            created_at=now,
            updated_at=now,
        )
        try:
            await self._cache.upsert(record)
        except Exception:  # pragma: no cover  best-effort cache write
            # Audit fix A12: use ``.exception`` so the stack trace lands
            # in the log — the cache is best-effort, but silent
            # ``.warning`` without exc_info erased the diagnostic trail
            # for rare upsert errors (unique-violation races, DB pool
            # exhaustion), making operator triage impossible.
            _logger.exception("media_cache_upsert_failed", source_url=source_url)
