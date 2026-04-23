"""A21: SqlAlchemyJobsRepository.update uses optimistic locking."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.entities.download_job import DownloadJob
from app.domain.enums import JobStatus, Platform
from app.exceptions import JobConcurrentUpdateError
from app.infrastructure.db.repositories.jobs_repo_impl import SqlAlchemyJobsRepository


class _FakeSession:
    def __init__(
        self,
        *,
        execute_results: list[object],
        existing_row: object | None = None,
    ) -> None:
        self.execute_results = execute_results
        self.existing_row = existing_row
        self.executed: list[object] = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return self.execute_results.pop(0)

    async def get(self, model, job_id: int):
        del model, job_id
        return self.existing_row


class _FakeBegin:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakeSessionMaker:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    def begin(self) -> _FakeBegin:
        return _FakeBegin(self.session)


class _ExecuteResult:
    def __init__(self, mapping: dict[str, object] | None) -> None:
        self.mapping = mapping

    def mappings(self) -> _ExecuteResult:
        return self

    def one_or_none(self) -> dict[str, object] | None:
        return self.mapping


def _job() -> DownloadJob:
    return DownloadJob(
        id=1,
        user_id=10,
        chat_id=20,
        source_url="https://youtube.com/watch?v=abc",
        platform=Platform.YOUTUBE,
        status=JobStatus.PROCESSING,
        status_version=2,
    )


@pytest.mark.asyncio
async def test_update_raises_job_concurrent_update_error_on_stale_version() -> None:
    existing = replace(_job(), status_version=3)
    session = _FakeSession(execute_results=[_ExecuteResult(None)], existing_row=existing)
    repo = SqlAlchemyJobsRepository(_FakeSessionMaker(session))  # type: ignore[arg-type]

    with pytest.raises(JobConcurrentUpdateError):
        await repo.update(_job())


@pytest.mark.asyncio
async def test_update_returns_entity_with_incremented_status_version() -> None:
    job = _job()
    mapping = {
        "id": job.id,
        "user_id": job.user_id,
        "chat_id": job.chat_id,
        "source_url": job.source_url,
        "platform": job.platform,
        "media_id": None,
        "title": None,
        "selected_option_key": None,
        "selected_format": None,
        "status": JobStatus.DONE,
        "file_path": None,
        "file_size": None,
        "mime_type": None,
        "telegram_file_id": None,
        "public_url": None,
        "error_message": None,
        "retries_count": 0,
        "status_version": 3,
        "extra": {},
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "completed_at": None,
    }
    session = _FakeSession(execute_results=[_ExecuteResult(mapping)])
    repo = SqlAlchemyJobsRepository(_FakeSessionMaker(session))  # type: ignore[arg-type]

    updated = await repo.update(job)

    assert updated.status_version == 3
    assert updated.status is JobStatus.DONE
