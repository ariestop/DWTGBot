"""
Provider-side abstractions exposed to the application layer.

Concrete providers (YouTube, Instagram, ...) live under
``app.infrastructure.providers``. The bot / use cases never import them directly;
they only depend on these protocols. This keeps the application layer testable
and platform-agnostic.
"""

from __future__ import annotations

from collections.abc import Callable
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

    async def probe_size(
        self,
        url: str,
        *,
        info: MediaInfo,
        option: DownloadOption,
    ) -> int | None:
        """Best-effort pre-download size probe for ``option``.

        Returns the expected bytes if the provider can determine them
        cheaply before downloading, otherwise ``None``.
        """
        ...

    async def download(
        self,
        url: str,
        option: DownloadOption,
        *,
        target_dir: str,
        on_progress: Callable[[float], None] | None = None,
        info: MediaInfo | None = None,
    ) -> DownloadResult:
        """Download the media into ``target_dir``.

        ``on_progress`` — optional sync callback invoked from the
        underlying downloader (yt-dlp) with a ``[0.0, 100.0]`` percent
        as the download advances. Must be thread-safe and
        non-blocking; providers pass it through unchanged. See
        ADR-0010 §2.2.

        ``info`` — the ``MediaInfo`` the job was analysed with. Providers
        may use its direct item URLs to skip another metadata request to
        the platform; they must fall back to their own extraction when
        those URLs no longer work.
        """
        ...


class ProviderRegistry(Protocol):
    """Looks up the right provider for a given platform."""

    def get(self, platform: Platform) -> Provider: ...
