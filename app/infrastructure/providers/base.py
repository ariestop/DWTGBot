"""Common scaffolding shared by concrete providers."""

from __future__ import annotations

import mimetypes
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.config import Settings
from app.domain.entities.media_info import (
    DownloadOption,
    DownloadResult,
    MediaInfo,
)
from app.domain.enums import MediaKind, Platform
from app.exceptions import DownloadError
from app.infrastructure.downloader.ytdlp_runner import YtDlpRunner
from app.infrastructure.storage.local_storage import LocalStorage
from app.logging_config import get_logger

_logger = get_logger(__name__)


class BaseProvider(ABC):
    platform: Platform

    def __init__(
        self,
        *,
        settings: Settings,
        ytdlp: YtDlpRunner,
        storage: LocalStorage,
    ) -> None:
        self._settings = settings
        self._ytdlp = ytdlp
        self._storage = storage

    @abstractmethod
    async def get_info(self, url: str) -> MediaInfo: ...

    @abstractmethod
    def build_options(self, info: MediaInfo) -> list[DownloadOption]: ...

    @abstractmethod
    def default_option(self, info: MediaInfo) -> DownloadOption:
        """Return the option chosen when the user skipped the picker UI.

        See ``app.application.services.providers.Provider.default_option``
        for the contract. Concrete providers MUST raise ``DownloadError``
        if no default exists for the given ``info`` — silent fallbacks
        mask upstream regressions (ADR-0010 §2.1).
        """
        ...

    @abstractmethod
    async def probe_size(
        self,
        url: str,
        *,
        info: MediaInfo,
        option: DownloadOption,
    ) -> int | None: ...

    @abstractmethod
    async def download(
        self,
        url: str,
        option: DownloadOption,
        *,
        target_dir: str,
        on_progress: Callable[[float], None] | None = None,
    ) -> DownloadResult: ...

    @staticmethod
    def _target_path(target_dir: str) -> Path:
        return Path(target_dir)

    @staticmethod
    def _cookie_extra_opts(configured: str | None, *, missing_event: str) -> dict[str, str] | None:
        """``{"cookiefile": path}`` for yt-dlp, or ``None`` when unset / missing.

        A configured-but-absent file logs ``missing_event`` and falls back to
        anonymous access instead of failing the request.
        """
        cookiefile = (configured or "").strip()
        if not cookiefile:
            return None
        cookie_path = Path(cookiefile)
        if not cookie_path.is_file():
            _logger.warning(missing_event, path=cookiefile)
            return None
        return {"cookiefile": str(cookie_path)}

    @staticmethod
    def _merge_extra_opts(
        extra_opts: Mapping[str, Any] | None, auth_opts: Mapping[str, str] | None
    ) -> dict[str, Any] | None:
        """Auth options win over per-call options on key clashes."""
        if extra_opts is None and auth_opts is None:
            return None
        return {**(extra_opts or {}), **(auth_opts or {})}

    @staticmethod
    def _result_from_files(
        files: list[Path],
        *,
        title: str,
        kind: MediaKind,
        done_event: str,
        empty_error: str,
    ) -> DownloadResult:
        """Build the ``DownloadResult`` for the files yt-dlp left in the job dir."""
        if not files:
            raise DownloadError(empty_error)
        total = sum(f.stat().st_size for f in files if f.exists())
        primary = files[0]
        mime = mimetypes.guess_type(primary.name)[0] or "application/octet-stream"
        _logger.info(
            done_event,
            files=len(files),
            total_bytes=total,
            primary=primary.name,
            mime=mime,
            kind=kind.value,
        )
        return DownloadResult(
            files=tuple(str(f) for f in files),
            total_size_bytes=total,
            primary_mime=mime,
            title=title,
            kind=kind,
        )
