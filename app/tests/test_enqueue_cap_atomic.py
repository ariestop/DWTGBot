"""Audit fix #2: per-user cap is enforced via a single atomic
``create_if_under_cap`` call, not a count + create pair.

The previous "count then create" pattern (kept here as the
``_RacyRepo`` baseline) loses races under concurrent ``/start`` clicks:
two coroutines each see ``active == cap-1`` and both insert. The new
contract returns ``None`` on cap saturation and is implemented in
production by ``pg_advisory_xact_lock``.
"""

from __future__ import annotations

import asyncio

import pytest

from app.application.dto.jobs import EnqueueDownloadInput
from app.application.use_cases.enqueue_download import EnqueueDownloadUseCase
from app.domain.entities.download_job import DownloadJob
from app.domain.enums import Platform
from app.domain.repositories.jobs_repo import JobsRepository
from app.exceptions import TooManyJobsError


class _AtomicRepo(JobsRepository):
    """Mirrors the production semantics: ``create_if_under_cap`` is the
    only mutator visible to ``execute``, the count and the insert
    happen under the same lock so concurrent calls see consistent
    state."""

    def __init__(self) -> None:
        self.active = 0
        self._lock = asyncio.Lock()
        self.created: list[DownloadJob] = []

    async def create(self, job: DownloadJob) -> DownloadJob:
        job.id = len(self.created) + 1
        self.created.append(job)
        return job

    async def get(self, job_id: int):
        return next((j for j in self.created if j.id == job_id), None)

    async def update(self, job: DownloadJob) -> DownloadJob:
        return job

    async def increment_retries(self, _: int) -> int:
        return 0

    async def count_active_for_user(self, _: int) -> int:
        return self.active

    async def create_if_under_cap(self, job: DownloadJob, *, cap: int) -> DownloadJob | None:
        # ``asyncio.Lock`` is the in-memory analogue of pg_advisory_xact_lock —
        # a single coroutine at a time runs the count/insert.
        async with self._lock:
            if self.active >= cap:
                return None
            await asyncio.sleep(0)  # force a context switch under the lock
            self.active += 1
            return await self.create(job)


class _FakeQueue:
    def __init__(self) -> None:
        self.payloads: list[object] = []

    async def enqueue_download(self, payload) -> None:  # type: ignore[no-untyped-def]
        self.payloads.append(payload)


def _payload() -> EnqueueDownloadInput:
    return EnqueueDownloadInput(
        user_id=1,
        chat_id=2,
        source_url="https://x.test/v",
        platform=Platform.YOUTUBE,
        selected_option_key="video_720",
        correlation_id="c",
    )


@pytest.mark.asyncio
async def test_concurrent_enqueue_respects_cap_under_atomic_repo() -> None:
    """Three concurrent coroutines, cap=2 → exactly two inserts, one
    rejection. Without the atomic repo all three would slip through."""
    repo = _AtomicRepo()
    queue = _FakeQueue()
    uc = EnqueueDownloadUseCase(jobs_repo=repo, queue=queue, max_concurrent_per_user=2)

    results = await asyncio.gather(
        uc.execute(_payload()),
        uc.execute(_payload()),
        uc.execute(_payload()),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, BaseException)]
    rejections = [r for r in results if isinstance(r, TooManyJobsError)]
    other = [
        r for r in results if isinstance(r, BaseException) and not isinstance(r, TooManyJobsError)
    ]

    assert len(successes) == 2
    assert len(rejections) == 1
    assert other == []
    assert len(queue.payloads) == 2
    assert len(repo.created) == 2
