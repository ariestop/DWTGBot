"""A retry after a successful delivery does not send the file again (audit R2).

The use case delivers, then writes DONE. If that write fails, arq
retries the job; with the delivery recorded in the ledger the retry
only finishes the bookkeeping.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from app.application.services.delivery_ledger import DeliveredRecord
from app.application.services.delivery_service import DeliveryOutcome
from app.application.use_cases.process_download import (
    ProcessDownloadInput,
    ProcessDownloadUseCase,
)
from app.config import get_settings
from app.domain.entities.download_job import DownloadJob
from app.domain.entities.media_info import DownloadOption, DownloadResult, MediaInfo
from app.domain.enums import DeliveryMethod, JobStatus, MediaKind, Platform

_OPTION = DownloadOption(
    key="video",
    label="Видео",
    kind=MediaKind.VIDEO,
    container="mp4",
    estimated_size_bytes=1024,
)


class _Repo:
    """Stores copies, like a database: a failed write changes nothing."""

    def __init__(self, job: DownloadJob, *, fail_done_writes: int) -> None:
        self._row = copy.deepcopy(job)
        self._fail_done_writes = fail_done_writes

    async def get(self, job_id: int) -> DownloadJob | None:
        return copy.deepcopy(self._row) if self._row.id == job_id else None

    async def update(self, job: DownloadJob) -> DownloadJob:
        if job.status is JobStatus.DONE and self._fail_done_writes:
            self._fail_done_writes -= 1
            raise ConnectionError("postgres blip")
        job.status_version += 1
        self._row = copy.deepcopy(job)
        return copy.deepcopy(job)

    @property
    def row(self) -> DownloadJob:
        return self._row


class _Provider:
    def __init__(self, tmp_path: Path) -> None:
        self._tmp = tmp_path
        self.downloads = 0

    def get(self, _platform: Platform) -> _Provider:
        return self

    async def get_info(self, url: str) -> MediaInfo:
        return MediaInfo(
            platform=Platform.INSTAGRAM, media_id="abc", title="Clip", kind=MediaKind.VIDEO
        )

    def build_options(self, info: MediaInfo) -> list[DownloadOption]:
        return [_OPTION]

    async def download(self, url: str, option: DownloadOption, **_: Any) -> DownloadResult:
        self.downloads += 1
        f = self._tmp / "clip.mp4"
        f.write_bytes(b"x" * 1024)
        return DownloadResult(
            files=(str(f),),
            total_size_bytes=1024,
            primary_mime="video/mp4",
            title="clip",
            kind=MediaKind.VIDEO,
        )


class _Storage:
    def __init__(self, tmp_path: Path) -> None:
        self._tmp = tmp_path

    def assert_free_space(self) -> None:
        return None

    def reset_job_dir(self, job_id: int) -> Path:
        return self._tmp


class _Delivery:
    def __init__(self) -> None:
        self.calls = 0

    async def deliver(self, *, job_id: int, chat_id: int, result: DownloadResult) -> Any:
        self.calls += 1
        return DeliveryOutcome(
            method=DeliveryMethod.TELEGRAM_UPLOAD,
            public_url=None,
            primary_telegram_file_id="file-id",
            delivered_path=result.files[0],
            file_size=1024,
        )


class _Ledger:
    def __init__(self) -> None:
        self.records: dict[int, DeliveredRecord] = {}

    async def record(self, *, job_id: int, delivered: DeliveredRecord) -> None:
        self.records[job_id] = delivered

    async def get(self, *, job_id: int) -> DeliveredRecord | None:
        return self.records.get(job_id)


class _Sender:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def send_text(self, _chat_id: int, text: str, **_: Any) -> None:
        self.texts.append(text)


def _setup(tmp_path: Path, *, fail_done_writes: int, ledger: _Ledger | None):  # type: ignore[no-untyped-def]
    job = DownloadJob(
        id=5,
        user_id=1,
        chat_id=2,
        source_url="https://www.instagram.com/p/abc/",
        platform=Platform.INSTAGRAM,
        selected_option_key="video",
    )
    repo = _Repo(job, fail_done_writes=fail_done_writes)
    provider = _Provider(tmp_path)
    delivery = _Delivery()
    sender = _Sender()
    use_case = ProcessDownloadUseCase(
        jobs_repo=repo,  # type: ignore[arg-type]
        providers=provider,  # type: ignore[arg-type]
        storage=_Storage(tmp_path),  # type: ignore[arg-type]
        delivery=delivery,  # type: ignore[arg-type]
        sender=sender,  # type: ignore[arg-type]
        settings=get_settings(),
        delivery_ledger=ledger,
    )
    return use_case, repo, provider, delivery, sender


@pytest.mark.asyncio
async def test_retry_after_delivery_only_writes_done(tmp_path: Path) -> None:
    ledger = _Ledger()
    use_case, repo, provider, delivery, sender = _setup(tmp_path, fail_done_writes=1, ledger=ledger)

    with pytest.raises(ConnectionError):
        await use_case.execute(ProcessDownloadInput(job_id=5, correlation_id="c", attempt=1))
    assert repo.row.status is JobStatus.PROCESSING
    assert delivery.calls == 1

    await use_case.execute(ProcessDownloadInput(job_id=5, correlation_id="c", attempt=2))

    assert delivery.calls == 1
    assert provider.downloads == 1
    assert repo.row.status is JobStatus.DONE
    assert repo.row.telegram_file_id == "file-id"
    assert repo.row.title == "Clip"
    assert repo.row.selected_format == "mp4"
    assert sender.texts == []


@pytest.mark.asyncio
async def test_without_a_ledger_the_retry_delivers_again(tmp_path: Path) -> None:
    # The behaviour the ledger prevents; also what happens when Redis is down.
    use_case, repo, provider, delivery, _ = _setup(tmp_path, fail_done_writes=1, ledger=None)

    with pytest.raises(ConnectionError):
        await use_case.execute(ProcessDownloadInput(job_id=5, correlation_id="c", attempt=1))
    await use_case.execute(ProcessDownloadInput(job_id=5, correlation_id="c", attempt=2))

    assert delivery.calls == 2
    assert provider.downloads == 2
    assert repo.row.status is JobStatus.DONE


@pytest.mark.asyncio
async def test_retry_without_a_record_runs_the_pipeline(tmp_path: Path) -> None:
    # The first attempt failed before delivering: nothing recorded.
    use_case, repo, provider, delivery, _ = _setup(tmp_path, fail_done_writes=0, ledger=_Ledger())
    first = await use_case._jobs.get(5)  # type: ignore[attr-defined]
    assert first is not None
    first.mark_processing()
    await use_case._jobs.update(first)  # type: ignore[attr-defined]

    await use_case.execute(ProcessDownloadInput(job_id=5, correlation_id="c", attempt=2))

    assert delivery.calls == 1
    assert provider.downloads == 1
    assert repo.row.status is JobStatus.DONE
