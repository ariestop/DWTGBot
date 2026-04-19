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

from app.application.dto.media import AnalyzedMedia
from app.application.services.providers import ProviderRegistry
from app.application.services.request_state_store import RequestStateStore
from app.logging_config import get_logger
from app.utils.url import detect

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AnalyzeLinkOutput:
    request_id: str
    analyzed: AnalyzedMedia


class AnalyzeLinkUseCase:
    def __init__(
        self,
        *,
        providers: ProviderRegistry,
        state_store: RequestStateStore,
        request_ttl_seconds: int,
    ) -> None:
        self._providers = providers
        self._state = state_store
        self._ttl = request_ttl_seconds

    async def execute(self, raw_input: str) -> AnalyzeLinkOutput:
        detected = detect(raw_input)
        provider = self._providers.get(detected.platform)

        info = await provider.get_info(detected.normalized)
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
        )
        return AnalyzeLinkOutput(request_id=request_id, analyzed=analyzed)
