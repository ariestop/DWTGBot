"""Provider registry — maps Platform enum to a concrete provider instance."""

from __future__ import annotations

from app.application.services.providers import Provider
from app.domain.enums import Platform
from app.exceptions import UnsupportedPlatformError


class DefaultProviderRegistry:
    def __init__(self, providers: list[Provider]) -> None:
        self._by_platform: dict[Platform, Provider] = {}
        for p in providers:
            self._by_platform[p.platform] = p

    def get(self, platform: Platform) -> Provider:
        try:
            return self._by_platform[platform]
        except KeyError as exc:
            raise UnsupportedPlatformError(f"No provider for platform: {platform}") from exc
