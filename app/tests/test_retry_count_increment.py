"""A22: task wrapper increments retries_count on every failed attempt."""

from __future__ import annotations

import pytest

from app.infrastructure.queue.tasks import process_download_job


class _StubUseCase:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.terminal_calls: list[tuple[int, BaseException]] = []

    async def execute(self, _payload: object) -> None:
        raise self._exc

    async def mark_terminally_failed(self, job_id: int, exc: BaseException) -> None:
        self.terminal_calls.append((job_id, exc))


class _RecordingJobsRepo:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def increment_retries(self, job_id: int) -> int:
        self.calls.append(job_id)
        return len(self.calls)


class _NoopMetrics:
    def inc_worker_active(self) -> None:
        return None

    def dec_worker_active(self) -> None:
        return None


@pytest.mark.asyncio
async def test_retry_count_is_incremented_on_mid_retry() -> None:
    boom = RuntimeError("transient")
    jobs_repo = _RecordingJobsRepo()
    ctx = {
        "use_case": _StubUseCase(boom),
        "jobs_repo": jobs_repo,
        "job_metrics": _NoopMetrics(),
        "job_try": 1,
        "max_tries": 3,
    }

    with pytest.raises(RuntimeError):
        await process_download_job(ctx, job_id=42, correlation_id="c")

    assert jobs_repo.calls == [42]


@pytest.mark.asyncio
async def test_retry_count_is_incremented_on_last_attempt_too() -> None:
    boom = RuntimeError("still transient")
    use_case = _StubUseCase(boom)
    jobs_repo = _RecordingJobsRepo()
    ctx = {
        "use_case": use_case,
        "jobs_repo": jobs_repo,
        "job_metrics": _NoopMetrics(),
        "job_try": 3,
        "max_tries": 3,
    }

    with pytest.raises(RuntimeError):
        await process_download_job(ctx, job_id=42, correlation_id="c")

    assert jobs_repo.calls == [42]
    assert use_case.terminal_calls == [(42, boom)]
