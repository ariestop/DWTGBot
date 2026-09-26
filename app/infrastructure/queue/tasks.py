"""arq task handlers. Bound to the worker process via WorkerSettings."""

from __future__ import annotations

from typing import Any

from arq import Retry

from app.application.services.job_metrics import JobMetrics, NoopJobMetrics
from app.application.use_cases.process_download import (
    ProcessDownloadInput,
    ProcessDownloadUseCase,
)
from app.domain.repositories.jobs_repo import JobsRepository
from app.logging_config import get_logger

_logger = get_logger(__name__)

# arq re-runs a job only when it raises ``Retry``; any other exception
# finishes it for good. Linear backoff gives an upstream that stalls
# bursts (Instagram) time to cool down: 20 s, then 40 s.
_RETRY_BASE_DELAY_S = 20


def _retry_delay_s(attempt: int) -> int:
    return _RETRY_BASE_DELAY_S * max(1, attempt)


async def process_download_job(ctx: dict[str, Any], job_id: int, correlation_id: str) -> None:
    """arq entrypoint. ``ctx`` is populated by WorkerSettings.on_startup.

    Two responsibilities live here that the use case can't own:

    1. ``worker_active_jobs`` gauge (ADR-0007 §2.7) — arq exposes no
       public "currently in flight" API; a process-local inc/dec around
       the use case is the only way to keep the SLO B4 saturation
       panel honest. ``finally`` guarantees the decrement even on
       timeout or unhandled exception.
    2. Terminal failure marking — ``ProcessDownloadUseCase._run``
       re-raises retryable / unexpected exceptions so arq can re-schedule
       with backoff. On the *last* attempt we have to translate the
       exception into a persisted ``FAILED`` row + user notification,
       otherwise the job would forever remain ``PROCESSING`` and the
       per-user concurrency cap would leak.
    """
    use_case: ProcessDownloadUseCase = ctx["use_case"]
    metrics: JobMetrics = ctx.get("job_metrics") or NoopJobMetrics()
    jobs_repo: JobsRepository | None = ctx.get("jobs_repo")
    # arq populates ``job_try`` (1-indexed); ``max_tries`` is put into the
    # shared ctx by ``WorkerSettings.on_startup``. We default to 1/1 when
    # the keys are absent so a misconfigured pool fails closed (mark
    # FAILED on first error) rather than silently looping.
    try_id = int(ctx.get("job_try", 1) or 1)
    max_tries = int(ctx.get("max_tries", 1) or 1)
    metrics.inc_worker_active()
    try:
        await use_case.execute(
            ProcessDownloadInput(job_id=job_id, correlation_id=correlation_id, attempt=try_id)
        )
    except Exception as exc:
        if jobs_repo is not None:
            try:
                retries_count = await jobs_repo.increment_retries(job_id)
                _logger.info(
                    "job_retry_count_incremented",
                    job_id=job_id,
                    retries_count=retries_count,
                )
            except Exception:  # pragma: no cover - best-effort
                _logger.exception("job_retry_count_increment_failed", job_id=job_id)
        if try_id >= max_tries:
            try:
                await use_case.mark_terminally_failed(job_id, exc)
            except Exception:  # pragma: no cover - best-effort
                _logger.exception(
                    "terminal_fail_marking_failed",
                    job_id=job_id,
                    correlation_id=correlation_id,
                )
            raise
        delay_s = _retry_delay_s(try_id)
        _logger.warning(
            "job_will_retry",
            job_id=job_id,
            correlation_id=correlation_id,
            attempt=try_id,
            max_tries=max_tries,
            delay_s=delay_s,
        )
        raise Retry(defer=delay_s) from exc
    finally:
        metrics.dec_worker_active()
