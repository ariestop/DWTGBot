"""A18: replayed jobs are ignored without side effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.application.use_cases.process_download import ProcessDownloadInput, ProcessDownloadUseCase
from app.config import get_settings
from app.domain.entities.download_job import DownloadJob
from app.domain.enums import JobStatus, Platform
from app.domain.observability import ReplayIgnoreReason


class _RecordingJobsRepo:
    def __init__(self, job: DownloadJob) -> None:
        self._job = job
        self.updates: list[DownloadJob] = []

    async def get(self, job_id: int) -> DownloadJob | None:
        return self._job if self._job.id == job_id else None

    async def update(self, job: DownloadJob) -> DownloadJob:
        self.updates.append(job)
        return job

    async def create(self, job: DownloadJob) -> DownloadJob:
        return job

    async def increment_retries(self, _job_id: int) -> int:
        return 0

    async def count_active_for_user(self, _user_id: int) -> int:
        return 0


class _ExplodingProviders:
    def get(self, _platform: Platform) -> Any:
        raise AssertionError("provider lookup must not happen for replayed jobs")


class _NoopSender:
    async def send_text(self, _chat_id: int, _text: str) -> None:
        return None


@dataclass
class _RecordingMetrics:
    replay_ignored: list[ReplayIgnoreReason]

    def __init__(self) -> None:
        self.replay_ignored = []

    def inc_replay_ignored(self, *, reason: ReplayIgnoreReason) -> None:
        self.replay_ignored.append(reason)

    def inc_status_change(self, **_: object) -> None:
        return None

    def observe_job_duration(self, **_: object) -> None:
        return None

    def inc_bot_message_handled(self, **_: object) -> None:
        return None

    def inc_job_created(self, **_: object) -> None:
        return None

    def set_queue_depth(self, **_: object) -> None:
        return None

    def inc_worker_active(self) -> None:
        return None

    def dec_worker_active(self) -> None:
        return None

    def set_worker_concurrency(self, **_: object) -> None:
        return None

    def inc_temp_link_serve(self, **_: object) -> None:
        return None


def _make_job(*, status: JobStatus) -> DownloadJob:
    return DownloadJob(
        id=42,
        user_id=1,
        chat_id=2,
        source_url="https://youtube.com/watch?v=abc",
        platform=Platform.YOUTUBE,
        selected_option_key="video_720",
        status=status,
    )


def _make_use_case(
    job: DownloadJob,
    metrics: _RecordingMetrics,
) -> tuple[ProcessDownloadUseCase, _RecordingJobsRepo]:
    repo = _RecordingJobsRepo(job)
    use_case = ProcessDownloadUseCase(
        jobs_repo=repo,  # type: ignore[arg-type]
        providers=_ExplodingProviders(),  # type: ignore[arg-type]
        storage=None,  # type: ignore[arg-type]
        delivery=None,  # type: ignore[arg-type]
        sender=_NoopSender(),  # type: ignore[arg-type]
        settings=get_settings(),
        metrics=metrics,  # type: ignore[arg-type]
    )
    return use_case, repo


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (JobStatus.DONE, ReplayIgnoreReason.DONE),
        (JobStatus.FAILED, ReplayIgnoreReason.FAILED),
        (JobStatus.PROCESSING, ReplayIgnoreReason.PROCESSING),
    ],
)
@pytest.mark.asyncio
async def test_replayed_job_is_ignored_without_side_effects(
    status: JobStatus,
    reason: ReplayIgnoreReason,
) -> None:
    metrics = _RecordingMetrics()
    use_case, repo = _make_use_case(_make_job(status=status), metrics)

    await use_case.execute(ProcessDownloadInput(job_id=42, correlation_id="rq"))

    assert repo.updates == []
    assert metrics.replay_ignored == [reason]
