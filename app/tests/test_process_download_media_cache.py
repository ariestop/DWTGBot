"""The worker reuses the bot's fresh analysis instead of asking the platform again."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.application.use_cases.process_download import (
    ProcessDownloadInput,
    ProcessDownloadUseCase,
)
from app.config import get_settings
from app.domain.entities.download_job import DownloadJob
from app.domain.entities.media_info import MediaInfo
from app.domain.enums import JobStatus, MediaKind, Platform
from app.domain.repositories.media_cache_repo import MediaCacheRecord

_URL = "https://www.instagram.com/p/DdrzVBqDScp/"
_IMG = "https://scontent-iad3-1.cdninstagram.com/v/t39.30808-6/photo.jpg"


class _Stop(Exception):
    """Ends the pipeline right after the analysed info is known."""


class _Repo:
    def __init__(self, job: DownloadJob) -> None:
        self._job = job

    async def get(self, job_id: int) -> DownloadJob | None:
        return self._job if self._job.id == job_id else None

    async def update(self, job: DownloadJob) -> DownloadJob:
        return job


class _Provider:
    def __init__(self) -> None:
        self.get_info_calls = 0
        self.seen: list[MediaInfo] = []

    def get(self, _platform: Platform) -> _Provider:
        return self

    async def get_info(self, _url: str) -> MediaInfo:
        self.get_info_calls += 1
        raise _Stop("provider asked")

    def build_options(self, info: MediaInfo) -> list[object]:
        self.seen.append(info)
        raise _Stop("options")


class _Cache:
    def __init__(self, record: MediaCacheRecord | None) -> None:
        self._record = record
        self.lookups: list[str] = []

    async def get_fresh(self, source_url: str) -> MediaCacheRecord | None:
        self.lookups.append(source_url)
        return self._record


def _record() -> MediaCacheRecord:
    now = datetime.now(UTC)
    return MediaCacheRecord(
        id=1,
        source_url=_URL,
        platform=Platform.INSTAGRAM,
        media_id="DdrzVBqDScp",
        title="Video by someone",
        metadata_json={
            "kind": "photo",
            "items": [{"kind": "photo", "url": _IMG}],
            "raw": {"is_gallery": False, "kinds": ["photo"]},
        },
        expires_at=now + timedelta(hours=1),
        created_at=now,
        updated_at=now,
    )


def _use_case(provider: _Provider, cache: _Cache) -> ProcessDownloadUseCase:
    job = DownloadJob(id=7, user_id=1, chat_id=1, source_url=_URL, platform=Platform.INSTAGRAM)
    assert job.status is JobStatus.PENDING
    return ProcessDownloadUseCase(
        jobs_repo=_Repo(job),  # type: ignore[arg-type]
        providers=provider,  # type: ignore[arg-type]
        storage=None,  # type: ignore[arg-type]
        delivery=None,  # type: ignore[arg-type]
        sender=None,  # type: ignore[arg-type]
        settings=get_settings(),
        media_cache=cache,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_worker_uses_fresh_cache_instead_of_provider_get_info() -> None:
    provider = _Provider()
    cache = _Cache(_record())

    with pytest.raises(_Stop, match="options"):
        await _use_case(provider, cache).execute(ProcessDownloadInput(job_id=7, correlation_id="c"))

    assert cache.lookups == [_URL]
    assert provider.get_info_calls == 0
    assert provider.seen[0].kind is MediaKind.PHOTO
    assert provider.seen[0].items[0].url == _IMG


@pytest.mark.asyncio
async def test_worker_falls_back_to_provider_on_cache_miss() -> None:
    provider = _Provider()

    with pytest.raises(_Stop, match="provider asked"):
        await _use_case(provider, _Cache(None)).execute(
            ProcessDownloadInput(job_id=7, correlation_id="c")
        )

    assert provider.get_info_calls == 1
