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

    def default_option(self, info: MediaInfo) -> DownloadOption:
        """Return the option to pick when the user skipped the picker UI.

        Used by the instant-download flow (see ADR-0010 §2.1): the
        ``AutoEnqueueDownloadUseCase`` calls ``default_option`` right
        after ``get_info`` and enqueues the job without asking the user.
        The returned option MUST be a member of ``build_options(info)``
        so the existing download pipeline handles it unchanged. If no
        sensible default exists (e.g. the platform returned a kind the
        provider does not know how to materialise) providers MUST raise
        ``DownloadError`` rather than silently falling back.
        """
        ...

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
