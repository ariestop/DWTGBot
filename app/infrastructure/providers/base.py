"""Common scaffolding shared by concrete providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
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
    async def download(
        self,
        url: str,
        option: DownloadOption,
        *,
        target_dir: str,
    ) -> DownloadResult: ...

    @staticmethod
    def _target_path(target_dir: str) -> Path:
        return Path(target_dir)
