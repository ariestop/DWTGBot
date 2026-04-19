"""ProcessDownloadUseCase — metrics + status_changed log emission paths.

We don't run the full ``_run`` path here (that requires real provider /
delivery / sender / repo wiring); instead we exercise ``_fail``
directly, which is the most error-prone instrumentation point — every
failure class has to emit exactly one ``jobs_status_changed_total{to=failed}``
with the right ``reason_class``.
"""

from __future__ import annotations

from app.application.use_cases.process_download import ProcessDownloadUseCase
from app.config import get_settings
from app.domain.entities.download_job import DownloadJob
from app.domain.enums import JobStatus, Platform
from app.domain.reason_class import ReasonClass


class _FakeJobsRepo:
    def __init__(self) -> None:
        self.updates: list[DownloadJob] = []

    async def update(self, job: DownloadJob) -> DownloadJob:
        self.updates.append(job)
        return job

    async def create(self, job):
        return job

    async def get(self, _id):
        return None

    async def count_active_for_user(self, _u):
        return 0

    async def increment_retries(self, _id):
        return 0


class _NoopSender:
    async def send_text(self, _chat_id: int, _text: str) -> None:
        return None

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class _RecordingMetrics:
    def __init__(self) -> None:
        self.status_changes: list[tuple[JobStatus, ReasonClass]] = []
        self.durations: list[tuple[str, float]] = []

    def inc_status_change(self, *, to: JobStatus, reason_class: ReasonClass) -> None:
        self.status_changes.append((to, reason_class))

    def observe_job_duration(self, *, file_size_class, seconds: float) -> None:
        self.durations.append((file_size_class.value, seconds))

    # Methods unused by ``_fail`` — present to satisfy the Protocol.
    def inc_bot_message_handled(self, **_):
        pass

    def inc_job_created(self, **_):
        pass

    def set_queue_depth(self, **_):
        pass

    def inc_worker_active(self):
        pass

    def dec_worker_active(self):
        pass

    def set_worker_concurrency(self, **_):
        pass

    def inc_temp_link_serve(self, **_):
        pass


def _make_use_case(metrics: _RecordingMetrics) -> ProcessDownloadUseCase:
    return ProcessDownloadUseCase(
        jobs_repo=_FakeJobsRepo(),  # type: ignore[arg-type]
        providers=None,  # type: ignore[arg-type]
        storage=None,  # type: ignore[arg-type]
        delivery=None,  # type: ignore[arg-type]
        sender=_NoopSender(),  # type: ignore[arg-type]
        settings=get_settings(),
        metrics=metrics,  # type: ignore[arg-type]
    )


def _make_job() -> DownloadJob:
    job = DownloadJob(
        id=1,
        user_id=1,
        chat_id=1,
        source_url="https://x.test/v",
        platform=Platform.YOUTUBE,
    )
    job.mark_processing()  # status starts as PROCESSING (after _transition_to)
    return job


async def test_fail_emits_status_change_with_reason() -> None:
    metrics = _RecordingMetrics()
    uc = _make_use_case(metrics)
    job = _make_job()

    await uc._fail(job, "user msg", "internal repr", ReasonClass.PROVIDER_ERROR)

    assert metrics.status_changes == [(JobStatus.FAILED, ReasonClass.PROVIDER_ERROR)]
    assert job.status is JobStatus.FAILED
    assert job.error_message == "internal repr"


async def test_fail_emits_status_change_for_user_error() -> None:
    """User errors must still be recorded — the SLO query filters them
    out at *aggregation* time (``reason_class != "user_error"``), not
    at emission time. Suppressing them here would erase the diagnostic
    "how many users sent us bad URLs today" panel."""
    metrics = _RecordingMetrics()
    uc = _make_use_case(metrics)
    job = _make_job()

    await uc._fail(job, "bad url", "InvalidUrlError(...)", ReasonClass.USER_ERROR)

    assert metrics.status_changes == [(JobStatus.FAILED, ReasonClass.USER_ERROR)]
