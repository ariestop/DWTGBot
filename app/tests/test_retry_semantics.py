"""Retry semantics for the worker pipeline (audit fix #1).

Three responsibilities are covered here, because they form a single
contract that arq + the use case + the task wrapper share:

1.  ``AppError.is_retryable`` — class attribute. Permanent vs transient
    must be encoded on the exception class itself, never inferred at
    call site.
2.  ``ProcessDownloadUseCase._run`` — re-raises retryable / unexpected
    exceptions so arq can re-schedule, calls ``_fail`` only on permanent
    errors.
3.  ``process_download_job`` — on the *last* arq attempt converts the
    raised exception into a persisted ``FAILED`` row via
    ``mark_terminally_failed``.

Without (3) a retryable exception on the last attempt would leak the
``PROCESSING`` row forever, the per-user concurrency cap would never
recover, and SLO A2 (failure rate) would silently under-count.
"""

from __future__ import annotations

import pytest

from app.application.use_cases.process_download import (
    ProcessDownloadInput,
    ProcessDownloadUseCase,
)
from app.config import get_settings
from app.domain.entities.download_job import DownloadJob
from app.domain.enums import JobStatus, Platform
from app.exceptions import (
    AppError,
    DownloadError,
    DownloadTimeoutError,
    FfmpegError,
    FileTooLargeError,
    InvalidUrlError,
    MediaNotFoundError,
    MediaPrivateError,
    ProviderError,
)
from app.infrastructure.queue.tasks import process_download_job

# ---------------------------------------------------------------------------
# (1) is_retryable matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_cls,expected",
    [
        # Transient: upstream/network/timeout — retry can succeed.
        (ProviderError, True),
        (DownloadError, True),
        # yt-dlp timeouts are terminal: retrying while the timed-out
        # background thread is still unwinding just keeps the user's
        # slot occupied longer.
        (DownloadTimeoutError, False),
        # Permanent: input/content/codec — retry will fail the same way.
        (MediaNotFoundError, False),
        (MediaPrivateError, False),
        (FileTooLargeError, False),
        (FfmpegError, False),
        (InvalidUrlError, False),
        # Base class default: opt-in to retries to avoid hidden looping.
        (AppError, False),
    ],
)
def test_is_retryable_matrix(exc_cls: type[AppError], expected: bool) -> None:
    assert exc_cls.is_retryable is expected
    # The flag must travel on instances too — it's read off the
    # instance in ProcessDownloadUseCase._run.
    assert exc_cls("boom").is_retryable is expected


# ---------------------------------------------------------------------------
# (2) ProcessDownloadUseCase._run — retryable vs permanent
# ---------------------------------------------------------------------------


class _RecordingJobsRepo:
    def __init__(self, job: DownloadJob) -> None:
        self._job = job
        # The use case mutates ``job`` in place, so we snapshot the
        # *status* on each update — comparing references would only
        # ever see the last value.
        self.update_statuses: list[JobStatus] = []

    async def get(self, job_id: int):
        return self._job if self._job.id == job_id else None

    async def update(self, job: DownloadJob) -> DownloadJob:
        self.update_statuses.append(job.status)
        return job

    async def create(self, job: DownloadJob) -> DownloadJob:  # pragma: no cover
        return job

    async def increment_retries(self, _: int) -> int:  # pragma: no cover
        return 0

    async def count_active_for_user(self, _: int) -> int:  # pragma: no cover
        return 0


class _RaisingProviders:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def get(self, _platform):
        return self

    async def get_info(self, _url):
        raise self._exc


class _NoopSender:
    async def send_text(self, _chat_id: int, _text: str) -> None:
        return None

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


def _make_job() -> DownloadJob:
    return DownloadJob(
        id=1,
        user_id=10,
        chat_id=20,
        source_url="https://x.test/v",
        platform=Platform.YOUTUBE,
    )


def _make_use_case(repo: _RecordingJobsRepo, exc: BaseException) -> ProcessDownloadUseCase:
    return ProcessDownloadUseCase(
        jobs_repo=repo,  # type: ignore[arg-type]
        providers=_RaisingProviders(exc),  # type: ignore[arg-type]
        storage=None,  # type: ignore[arg-type]
        delivery=None,  # type: ignore[arg-type]
        sender=_NoopSender(),  # type: ignore[arg-type]
        settings=get_settings(),
    )


@pytest.mark.asyncio
async def test_run_re_raises_retryable_apperror() -> None:
    """Retryable AppError must propagate so arq counts ``job_try`` and
    the wrapper can decide whether to mark FAILED on the last attempt."""
    job = _make_job()
    repo = _RecordingJobsRepo(job)
    uc = _make_use_case(repo, DownloadError("transient"))

    with pytest.raises(DownloadError):
        await uc.execute(ProcessDownloadInput(job_id=1, correlation_id="c"))

    # _run only persisted the PROCESSING transition — *not* a FAILED row.
    assert repo.update_statuses == [JobStatus.PROCESSING]


@pytest.mark.asyncio
async def test_run_marks_failed_for_permanent_apperror() -> None:
    """Permanent AppError is terminal here: ``_fail`` writes FAILED
    + notifies the user, the exception is *not* re-raised so arq
    won't re-schedule."""
    job = _make_job()
    repo = _RecordingJobsRepo(job)
    uc = _make_use_case(repo, MediaPrivateError("private"))

    await uc.execute(ProcessDownloadInput(job_id=1, correlation_id="c"))

    assert repo.update_statuses == [JobStatus.PROCESSING, JobStatus.FAILED]


@pytest.mark.asyncio
async def test_run_re_raises_unexpected_exception() -> None:
    """Anything that isn't an AppError must be treated as retryable —
    arq will re-schedule, the wrapper will mark FAILED on the last
    attempt. Persisting FAILED here would defeat both."""
    job = _make_job()
    repo = _RecordingJobsRepo(job)
    uc = _make_use_case(repo, RuntimeError("kaboom"))

    with pytest.raises(RuntimeError):
        await uc.execute(ProcessDownloadInput(job_id=1, correlation_id="c"))

    assert repo.update_statuses == [JobStatus.PROCESSING]


# ---------------------------------------------------------------------------
# (3) process_download_job — terminal mark on last attempt
# ---------------------------------------------------------------------------


class _StubUseCase:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.terminal_calls: list[tuple[int, BaseException]] = []

    async def execute(self, _payload):
        raise self._exc

    async def mark_terminally_failed(self, job_id: int, exc: BaseException) -> None:
        self.terminal_calls.append((job_id, exc))


class _NoopMetrics:
    def __init__(self) -> None:
        self.active = 0

    def inc_worker_active(self) -> None:
        self.active += 1

    def dec_worker_active(self) -> None:
        self.active -= 1


@pytest.mark.asyncio
async def test_task_marks_terminal_on_last_attempt() -> None:
    boom = DownloadError("transient")
    uc = _StubUseCase(boom)
    metrics = _NoopMetrics()
    ctx = {"use_case": uc, "job_metrics": metrics, "job_try": 3, "max_tries": 3}

    with pytest.raises(DownloadError):
        await process_download_job(ctx, job_id=42, correlation_id="c")

    assert uc.terminal_calls == [(42, boom)]
    assert metrics.active == 0  # gauge balanced even on exception


@pytest.mark.asyncio
async def test_task_does_not_mark_when_more_attempts_remain() -> None:
    """Mid-retry path — ``_fail`` must NOT run, otherwise arq would
    re-schedule a job that is already in the FAILED terminal state."""
    boom = DownloadError("transient")
    uc = _StubUseCase(boom)
    metrics = _NoopMetrics()
    ctx = {"use_case": uc, "job_metrics": metrics, "job_try": 1, "max_tries": 3}

    with pytest.raises(DownloadError):
        await process_download_job(ctx, job_id=42, correlation_id="c")

    assert uc.terminal_calls == []
    assert metrics.active == 0


@pytest.mark.asyncio
async def test_task_defaults_to_marking_when_ctx_keys_missing() -> None:
    """If the ctx is misconfigured (no ``job_try``/``max_tries`` —
    happens with a hand-crafted pool in tests), default to "this is the
    last attempt" so we fail-closed and don't loop forever silently."""
    boom = DownloadError("transient")
    uc = _StubUseCase(boom)
    metrics = _NoopMetrics()
    ctx: dict = {"use_case": uc, "job_metrics": metrics}

    with pytest.raises(DownloadError):
        await process_download_job(ctx, job_id=42, correlation_id="c")

    assert uc.terminal_calls == [(42, boom)]
