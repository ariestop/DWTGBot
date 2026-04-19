"""arq task handlers. Bound to the worker process via WorkerSettings."""

from __future__ import annotations

from typing import Any

from app.application.services.job_metrics import JobMetrics, NoopJobMetrics
from app.application.use_cases.process_download import (
    ProcessDownloadInput,
    ProcessDownloadUseCase,
)
from app.logging_config import get_logger

_logger = get_logger(__name__)


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
    metrics.inc_worker_active()
    try:
        await use_case.execute(ProcessDownloadInput(job_id=job_id, correlation_id=correlation_id))
    except Exception as exc:
        # arq populates ``job_try`` (1-indexed) and (optionally)
        # ``max_tries`` per call. We default to 1/1 when the keys are
        # absent so a misconfigured pool fails closed (mark FAILED on
        # first error) rather than silently looping.
        try_id = int(ctx.get("job_try", 1) or 1)
        max_tries = int(ctx.get("max_tries", 1) or 1)
        if try_id >= max_tries:
            try:
                await use_case.mark_terminally_failed(job_id, exc)
            except Exception:  # pragma: no cover - best-effort
                _logger.exception(
                    "terminal_fail_marking_failed",
                    job_id=job_id,
                    correlation_id=correlation_id,
                )
        else:
            _logger.warning(
                "job_will_retry",
                job_id=job_id,
                correlation_id=correlation_id,
                attempt=try_id,
                max_tries=max_tries,
            )
        raise
    finally:
        metrics.dec_worker_active()
