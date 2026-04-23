"""A21: ProcessDownloadUseCase handles optimistic-lock conflicts defensively."""

from __future__ import annotations

import pytest

from app.application.use_cases.process_download import ProcessDownloadInput, ProcessDownloadUseCase
from app.config import get_settings
from app.domain.entities.download_job import DownloadJob
from app.domain.enums import JobStatus, Platform
from app.exceptions import JobConcurrentUpdateError


class _ConflictRepo:
    def __init__(self) -> None:
        self.initial = DownloadJob(
            id=42,
            user_id=1,
            chat_id=2,
            source_url="https://youtube.com/watch?v=abc",
            platform=Platform.YOUTUBE,
            selected_option_key="video_720",
            status=JobStatus.PENDING,
        )
        self.latest = DownloadJob(
            id=42,
            user_id=1,
            chat_id=2,
            source_url="https://youtube.com/watch?v=abc",
            platform=Platform.YOUTUBE,
            selected_option_key="video_720",
            status=JobStatus.DONE,
            status_version=1,
        )
        self.update_calls = 0

    async def get(self, job_id: int) -> DownloadJob | None:
        if job_id != 42:
            return None
        return self.initial if self.update_calls == 0 else self.latest

    async def update(self, job: DownloadJob) -> DownloadJob:
        self.update_calls += 1
        raise JobConcurrentUpdateError(f"stale version for {job.id}")


class _ExplodingProviders:
    def get(self, _platform: Platform):
        raise AssertionError("provider lookup must not happen after transition conflict")


class _NoopSender:
    async def send_text(self, _chat_id: int, _text: str) -> None:
        return None


class _NoopMetrics:
    def inc_replay_ignored(self, **_: object) -> None:
        return None

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

    def inc_storage_orphan_dirs_removed(self, **_: object) -> None:
        return None


@pytest.mark.asyncio
async def test_transition_conflict_with_latest_done_becomes_noop() -> None:
    repo = _ConflictRepo()
    use_case = ProcessDownloadUseCase(
        jobs_repo=repo,  # type: ignore[arg-type]
        providers=_ExplodingProviders(),  # type: ignore[arg-type]
        storage=None,  # type: ignore[arg-type]
        delivery=None,  # type: ignore[arg-type]
        sender=_NoopSender(),  # type: ignore[arg-type]
        settings=get_settings(),
        metrics=_NoopMetrics(),  # type: ignore[arg-type]
    )

    await use_case.execute(ProcessDownloadInput(job_id=42, correlation_id="rq"))

    assert repo.update_calls == 1
