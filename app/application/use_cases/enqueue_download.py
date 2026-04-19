"""
Persist a DownloadJob and push it onto the worker queue.

This is invoked by the bot's callback handler after the user picks a
download option. The use case is intentionally thin: bookkeeping +
publish. Heavy lifting (fetch, ffmpeg, deliver) happens in the worker.
"""

from __future__ import annotations

from app.application.dto.jobs import (
    EnqueueDownloadInput,
    EnqueueDownloadResult,
    WorkerJobPayload,
)
from app.application.services.job_metrics import JobMetrics, NoopJobMetrics
from app.application.services.queue import QueueProducer
from app.domain.entities.download_job import DownloadJob
from app.domain.repositories.jobs_repo import JobsRepository
from app.exceptions import TooManyJobsError
from app.logging_config import get_logger

_logger = get_logger(__name__)


class EnqueueDownloadUseCase:
    """Persist + enqueue. Also enforces the per-user concurrency cap
    (`MAX_CONCURRENT_JOBS_PER_USER`) — see `docs/37-load-and-capacity.md`."""

    def __init__(
        self,
        *,
        jobs_repo: JobsRepository,
        queue: QueueProducer,
        max_concurrent_per_user: int,
        metrics: JobMetrics | None = None,
    ) -> None:
        if max_concurrent_per_user < 1:
            raise ValueError("max_concurrent_per_user must be >= 1")
        self._jobs = jobs_repo
        self._queue = queue
        self._max_per_user = max_concurrent_per_user
        # Default to Noop so existing tests that construct the use case
        # without metrics keep passing. Production wiring in
        # ``composition.build_bot`` always passes a real sink.
        self._metrics: JobMetrics = metrics if metrics is not None else NoopJobMetrics()

    async def execute(self, payload: EnqueueDownloadInput) -> EnqueueDownloadResult:
        job = DownloadJob(
            id=None,
            user_id=payload.user_id,
            chat_id=payload.chat_id,
            source_url=payload.source_url,
            platform=payload.platform,
            selected_option_key=payload.selected_option_key,
        )
        # Atomic check-then-insert under a per-user advisory lock —
        # closes the race window that the previous count/create pair
        # left open (audit fix #2). Returns ``None`` when the cap is
        # already saturated.
        created = await self._jobs.create_if_under_cap(job, cap=self._max_per_user)
        if created is None:
            _logger.info(
                "enqueue_rejected_per_user_cap",
                user_id=payload.user_id,
                cap=self._max_per_user,
            )
            raise TooManyJobsError(
                f"user {payload.user_id} is at the per-user cap ({self._max_per_user})"
            )
        assert created.id is not None  # repo contract

        await self._queue.enqueue_download(
            WorkerJobPayload(job_id=created.id, correlation_id=payload.correlation_id)
        )
        # Metric BEFORE the log so a Prometheus scrape sees the bump
        # in the same window as ``job_enqueued`` shows up in Loki.
        self._metrics.inc_job_created(platform=payload.platform)
        _logger.info(
            "job_enqueued",
            job_id=created.id,
            platform=payload.platform.value,
            option=payload.selected_option_key,
            user_id=payload.user_id,
        )
        return EnqueueDownloadResult(job_id=created.id)
