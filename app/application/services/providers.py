"""
Provider-side abstractions exposed to the application layer.

Concrete providers (YouTube, Instagram, ...) live under
``app.infrastructure.providers``. The bot / use cases never import them directly;
they only depend on these protocols. This keeps the application layer testable
and platform-agnostic.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.entities.media_info import (
    DownloadOption,
    DownloadResult,
    MediaInfo,
)
from app.domain.enums import Platform


class Provider(Protocol):
    """One concrete media platform integration."""

    platform: Platform

    async def get_info(self, url: str) -> MediaInfo: ...

    def build_options(self, info: MediaInfo) -> list[DownloadOption]: ...

    async def download(
        self,
        url: str,
        option: DownloadOption,
        *,
        target_dir: str,
    ) -> DownloadResult: ...


class ProviderRegistry(Protocol):
    """Looks up the right provider for a given platform."""

    def get(self, platform: Platform) -> Provider: ...
