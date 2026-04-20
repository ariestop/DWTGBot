"""
Periodic cleanup worker.

Runs as a long-lived process (separate container) and performs:
  - deactivate temp links whose ``expires_at`` has passed,
  - delete files associated with already-inactive temp links,
  - purge expired media_cache rows,
  - reap orphan ``PROCESSING`` jobs (S1 audit fix).

The interval is set by ``CLEANUP_INTERVAL_SECONDS``.

L13 (audit fix): uses ``composition.build_cleanup`` instead of
``build_api`` — no /metrics server, no arq pool, no dev-only shims.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from app.composition import CleanupComposition, build_cleanup
from app.config import get_settings
from app.logging_config import configure_logging, get_logger
from app.observability.sentry import configure_sentry


async def _run_cycle(
    composition: CleanupComposition,
    *,
    orphan_age_seconds: int,
) -> None:
    log = get_logger("dwtgbot.cleanup")

    deactivated = await composition.temp_links_repo.deactivate_expired()
    if deactivated:
        log.info("temp_links_deactivated", count=deactivated)

    inactive = await composition.temp_links_repo.list_inactive_with_files(limit=500)
    removed_files = 0
    for link in inactive:
        path = Path(link.file_path)
        if path.exists():
            try:
                composition.storage.remove_path(path)
                removed_files += 1
            except Exception:  # pragma: no cover  best-effort
                log.exception("temp_link_file_remove_failed", path=str(path))
    if removed_files:
        log.info("temp_link_files_removed", count=removed_files)

    purged = await composition.media_cache_repo.purge_expired()
    if purged:
        log.info("media_cache_purged", count=purged)

    # S1 (audit fix): mark stuck PROCESSING jobs FAILED so they release
    # the per-user cap. Logged at WARNING because a non-zero count
    # always indicates a worker death — operators should investigate.
    reaped = await composition.jobs_repo.reap_orphan_processing(
        older_than_seconds=orphan_age_seconds,
    )
    if reaped:
        log.warning("orphan_jobs_reaped", count=reaped, age_threshold_s=orphan_age_seconds)


async def _amain() -> int:
    settings = get_settings()
    configure_logging(settings)
    configure_sentry(settings, role="cleanup")
    log = get_logger("dwtgbot.cleanup")

    errors = settings.validate_runtime(require_storage=True, require_tools=False)
    if errors:
        for err in errors:
            log.error("cleanup_startup_check_failed", reason=err)
        return 2

    composition = await build_cleanup(settings)

    stop = asyncio.Event()

    def _stop(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _stop)

    log.info(
        "cleanup_started",
        interval_sec=settings.CLEANUP_INTERVAL_SECONDS,
        orphan_age_sec=settings.ORPHAN_JOB_AGE_SECONDS,
    )
    try:
        while not stop.is_set():
            try:
                await _run_cycle(
                    composition,
                    orphan_age_seconds=settings.ORPHAN_JOB_AGE_SECONDS,
                )
            except Exception:  # pragma: no cover
                log.exception("cleanup_cycle_error")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.CLEANUP_INTERVAL_SECONDS)
            except TimeoutError:
                continue
    finally:
        await composition.aclose()
        log.info("cleanup_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
