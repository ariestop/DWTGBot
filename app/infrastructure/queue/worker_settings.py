"""arq WorkerSettings, used by ``arq app.infrastructure.queue.worker_settings.WorkerSettings``.

We construct the worker-side composition once on startup, store it in the
arq context, and shut it down on the worker stop signal.
"""

from __future__ import annotations

from typing import Any

from app.composition import build_worker
from app.config import get_settings
from app.infrastructure.queue.arq_pool import WORKER_QUEUE_NAME, build_redis_settings
from app.infrastructure.queue.tasks import process_download_job
from app.logging_config import configure_logging, get_logger
from app.observability.sentry import configure_sentry

_logger = get_logger(__name__)


async def _on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings)
    configure_sentry(settings, role="worker")
    errors = settings.validate_runtime(require_storage=True, require_tools=True)
    if errors:
        for err in errors:
            _logger.error("worker_startup_check_failed", reason=err)
        raise RuntimeError("Worker startup checks failed: " + "; ".join(errors))

    composition = build_worker(settings)
    await composition.sender.initialize()
    # Start /metrics first so the worker is observable from the moment
    # the first job runs. Noop'd when METRICS_ENABLED=false (server is
    # None — ``composition.metrics_server`` stays None too).
    if composition.metrics_server is not None:
        start = getattr(composition.metrics_server, "start", None)
        if start is not None:
            await start()
    ctx["composition"] = composition
    ctx["use_case"] = composition.use_case
    # Surfaced into ctx so ``process_download_job`` can inc/dec the
    # worker_active_jobs gauge without having to reach into the
    # composition object on every task.
    ctx["job_metrics"] = composition.job_metrics
    _logger.info("worker_started", concurrency=settings.WORKER_CONCURRENCY)


async def _on_shutdown(ctx: dict[str, Any]) -> None:
    composition = ctx.get("composition")
    if composition is not None:
        await composition.aclose()
    _logger.info("worker_stopped")


class WorkerSettings:
    """Picked up by arq's CLI."""

    functions = [process_download_job]
    on_startup = _on_startup
    on_shutdown = _on_shutdown
    redis_settings = build_redis_settings(get_settings())
    max_jobs = get_settings().WORKER_CONCURRENCY
    job_timeout = get_settings().JOB_TIMEOUT_SECONDS
    max_tries = max(1, get_settings().JOB_MAX_RETRIES + 1)
    keep_result = 0
    queue_name = WORKER_QUEUE_NAME
