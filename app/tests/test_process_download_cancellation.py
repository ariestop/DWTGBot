"""Worker honours the cooperative cancel flag (ADR-0010 §2.1).

The bot's cancel-button handler writes ``cancel:{job_id}=1`` to Redis
and publishes a terminal CANCELLED event to the progress channel. The
worker is expected to notice the flag at the next phase boundary and
raise :class:`JobCancelledError`, which the use-case classifies as a
``user_error`` (non-retryable) and marks the row FAILED with a
cancelled-by-user reason.

This module reuses the fake collaborators from
``test_process_download_progress.py`` so only the cancellation wiring
is under test here.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.application.services.job_cancellation import JobCancellationStore
from app.application.use_cases.process_download import (
    ProcessDownloadInput,
    ProcessDownloadUseCase,
)
from app.config import get_settings
from app.domain.enums import JobStatus
from app.tests.test_process_download_progress import (
    _FakeDelivery,
    _FakeJobsRepo,
    _FakeProvider,
    _FakeProviderRegistry,
    _FakeSender,
    _FakeStorage,
    _make_info,
    _make_job,
    _make_option,
    _make_result,
    _RecordingReporter,
)


class _FakeCancellationStore(JobCancellationStore):
    """Programmable cancel flag — drives the worker down the cancel path."""

    def __init__(self, *, cancelled_job_ids: set[int] | None = None) -> None:
        self._cancelled = set(cancelled_job_ids or set())
        self.cleared: list[int] = []

    async def request(self, *, job_id: int) -> None:
        self._cancelled.add(job_id)

    async def is_cancelled(self, *, job_id: int) -> bool:
        return job_id in self._cancelled

    async def clear(self, *, job_id: int) -> None:
        self.cleared.append(job_id)


def _make_cancellable_use_case(
    reporter: _RecordingReporter,
    cancellation: JobCancellationStore,
    tmp_path: str,
) -> tuple[ProcessDownloadUseCase, _FakeJobsRepo, _FakeProvider]:
    job = _make_job()
    repo = _FakeJobsRepo(job)
    prov = _FakeProvider(_make_info(), _make_option(), _make_result())
    registry = _FakeProviderRegistry(prov)
    use_case = ProcessDownloadUseCase(
        jobs_repo=repo,  # type: ignore[arg-type]
        providers=registry,  # type: ignore[arg-type]
        storage=_FakeStorage(tmp_path),  # type: ignore[arg-type]
        delivery=_FakeDelivery(),  # type: ignore[arg-type]
        sender=_FakeSender(),  # type: ignore[arg-type]
        settings=get_settings(),
        progress_reporter=reporter,
        cancellation=cancellation,
    )
    return use_case, repo, prov


@pytest.mark.asyncio
async def test_cancel_flag_stops_worker_before_download(tmp_path: Any) -> None:
    """With the flag set, the worker must NOT call ``provider.download``
    and the job row must land in FAILED with a cancelled-by-user
    reason."""
    reporter = _RecordingReporter()
    cancellation = _FakeCancellationStore(cancelled_job_ids={42})
    use_case, repo, prov = _make_cancellable_use_case(reporter, cancellation, str(tmp_path))

    await use_case.execute(ProcessDownloadInput(job_id=42, correlation_id="r"))

    # Provider must have been queried for info (pre-download phases run)
    # but ``download`` is fenced by the DOWNLOADING-phase cancel check.
    # ``_FakeProvider.download`` is never called because ``_check_cancel``
    # raises before we get there — ``received_on_progress`` stays None.
    assert prov.received_on_progress is None

    # Job row reconciled to FAILED with the cancel-specific error
    # message. The status enum is reused (no new CANCELLED value to
    # migrate into); the reporter is what flips the user-visible UI.
    assert any(u.status is JobStatus.FAILED for u in repo.updates)
    last = repo.updates[-1]
    assert last.error_message == "cancelled by user"

    # Flag cleared so a recycled job_id (shouldn't happen today, but
    # the cleanup is defensive) does not inherit the stale flag.
    assert cancellation.cleared == [42]

    # Reporter must NOT have fired ``fail`` — that would render the
    # failure text in the UI. The bot emits ``cancel`` itself from
    # the button handler (not exercised in this worker-side test).
    fail_calls = [c for c in reporter.calls if c.method == "fail"]
    assert fail_calls == []


@pytest.mark.asyncio
async def test_no_cancellation_store_leaves_worker_unchanged(tmp_path: Any) -> None:
    """Legacy (picker) branch builds the use-case without a cancel
    store; the worker must still run a clean happy path."""
    reporter = _RecordingReporter()
    use_case, repo, prov = _make_cancellable_use_case(
        reporter, cancellation=_FakeCancellationStore(), tmp_path=str(tmp_path)
    )
    # Drop the cancellation store to emulate legacy wiring.
    use_case._cancellation = None  # type: ignore[attr-defined]

    await use_case.execute(ProcessDownloadInput(job_id=42, correlation_id="r"))

    # Happy path: provider.download called, finish emitted, job DONE.
    assert prov.received_on_progress is not None
    assert any(c.method == "finish" for c in reporter.calls)
    assert any(u.status is JobStatus.DONE for u in repo.updates)
