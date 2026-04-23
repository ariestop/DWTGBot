"""Common scaffolding shared by concrete providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.domain.entities.media_info import (
    DownloadOption,
    DownloadResult,
    MediaInfo,
)
from app.domain.enums import Platform
from app.infrastructure.downloader.ytdlp_runner import YtDlpRunner
from app.infrastructure.storage.local_storage import LocalStorage


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
