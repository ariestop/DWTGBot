"""EnqueueDownloadUseCase — pure orchestration (no DB/queue)."""

from __future__ import annotations

import pytest

from app.application.dto.jobs import EnqueueDownloadInput, WorkerJobPayload
from app.application.use_cases.enqueue_download import EnqueueDownloadUseCase
from app.domain.entities.download_job import DownloadJob
from app.domain.enums import JobStatus, Platform
from app.domain.repositories.jobs_repo import JobsRepository
from app.exceptions import TooManyJobsError


class _FakeJobsRepo(JobsRepository):
    def __init__(self, *, active_for_user: int = 0) -> None:
        self.created: list[DownloadJob] = []
        self._active_for_user = active_for_user

    async def create(self, job: DownloadJob) -> DownloadJob:
        job.id = len(self.created) + 100
        self.created.append(job)
        return job

    async def get(self, job_id: int):
        for j in self.created:
            if j.id == job_id:
                return j
        return None

    async def update(self, job: DownloadJob) -> DownloadJob:
        return job

    async def increment_retries(self, job_id: int) -> int:
        return 0

    async def count_active_for_user(self, user_id: int) -> int:
        return self._active_for_user

    async def create_if_under_cap(self, job: DownloadJob, *, cap: int) -> DownloadJob | None:
        # In-memory mirror of the SQL implementation's contract:
        # serialise check + insert behind a single coroutine call.
        if self._active_for_user >= cap:
            return None
        return await self.create(job)

    async def reap_orphan_processing(self, *, older_than_seconds: int) -> int:
        # S1 (audit fix): not exercised by the enqueue path; the cleanup
        # worker calls this. The fake just satisfies the ABC contract.
        del older_than_seconds
        return 0


class _FakeQueue:
    def __init__(self) -> None:
        self.payloads: list[WorkerJobPayload] = []

    async def enqueue_download(self, payload: WorkerJobPayload) -> None:
        self.payloads.append(payload)


def _make_input() -> EnqueueDownloadInput:
    return EnqueueDownloadInput(
        user_id=1,
        chat_id=2,
        source_url="https://youtu.be/x",
        platform=Platform.YOUTUBE,
        selected_option_key="video_720",
        correlation_id="corr-1",
    )


@pytest.mark.asyncio
async def test_enqueue_persists_and_publishes() -> None:
    repo = _FakeJobsRepo()
    queue = _FakeQueue()
    uc = EnqueueDownloadUseCase(jobs_repo=repo, queue=queue, max_concurrent_per_user=2)

    out = await uc.execute(_make_input())

    assert out.job_id == 100
    assert len(repo.created) == 1
    job = repo.created[0]
    assert job.status is JobStatus.PENDING
    assert job.selected_option_key == "video_720"
    assert queue.payloads == [WorkerJobPayload(job_id=100, correlation_id="corr-1")]


@pytest.mark.asyncio
async def test_enqueue_rejects_when_per_user_cap_hit() -> None:
    repo = _FakeJobsRepo(active_for_user=2)
    queue = _FakeQueue()
    uc = EnqueueDownloadUseCase(jobs_repo=repo, queue=queue, max_concurrent_per_user=2)

    with pytest.raises(TooManyJobsError):
        await uc.execute(_make_input())

    assert repo.created == []
    assert queue.payloads == []


@pytest.mark.asyncio
async def test_enqueue_allows_when_below_cap() -> None:
    repo = _FakeJobsRepo(active_for_user=1)
    queue = _FakeQueue()
    uc = EnqueueDownloadUseCase(jobs_repo=repo, queue=queue, max_concurrent_per_user=2)

    out = await uc.execute(_make_input())

    assert out.job_id == 100
    assert len(queue.payloads) == 1


def test_enqueue_constructor_rejects_zero_cap() -> None:
    repo = _FakeJobsRepo()
    queue = _FakeQueue()
    with pytest.raises(ValueError):
        EnqueueDownloadUseCase(jobs_repo=repo, queue=queue, max_concurrent_per_user=0)
