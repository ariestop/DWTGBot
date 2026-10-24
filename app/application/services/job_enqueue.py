"""Push a freshly inserted download job onto the worker queue.

Both enqueue paths (picker and instant) insert the row first and publish
second. If the publish fails (Redis down), the row would stay PENDING
forever and hold one of the user's ``MAX_CONCURRENT_JOBS_PER_USER``
slots, so the user is told "too many jobs" on every later link.
``enqueue_or_release`` marks such a row FAILED before re-raising; the
cleanup worker's PENDING reaper covers the case where that write fails
too.
"""

from __future__ import annotations

from app.application.dto.jobs import WorkerJobPayload
from app.application.services.queue import QueueProducer
from app.domain.entities.download_job import DownloadJob
from app.domain.repositories.jobs_repo import JobsRepository
from app.logging_config import get_logger

_logger = get_logger(__name__)


async def enqueue_or_release(
    *,
    jobs: JobsRepository,
    queue: QueueProducer,
    job: DownloadJob,
    correlation_id: str,
) -> None:
    """Publish ``job``; on failure mark it FAILED and re-raise."""
    if job.id is None:
        raise ValueError("enqueue_or_release needs a persisted job")
    try:
        await queue.enqueue_download(WorkerJobPayload(job_id=job.id, correlation_id=correlation_id))
    except Exception as exc:
        job.mark_failed(f"enqueue failed: {type(exc).__name__}")
        try:
            await jobs.update(job)
        except Exception:
            _logger.exception("enqueue_failed_job_not_released", job_id=job.id)
        else:
            _logger.warning(
                "enqueue_failed_job_released",
                job_id=job.id,
                error_class=type(exc).__name__,
            )
        raise
