"""
Periodic cleanup worker.

Runs as a long-lived process (separate container) and performs:
  - deactivate temp links whose ``expires_at`` has passed,
  - delete files associated with already-inactive temp links,
  - purge expired media_cache rows.

The interval is set by ``CLEANUP_INTERVAL_SECONDS``.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from app.composition import build_api
from app.config import get_settings
from app.infrastructure.db.repositories.media_cache_repo_impl import (
    SqlAlchemyMediaCacheRepository,
)
from app.logging_config import configure_logging, get_logger


async def _run_cycle(composition, media_cache_repo) -> None:
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

    purged = await media_cache_repo.purge_expired()
    if purged:
        log.info("media_cache_purged", count=purged)


async def _amain() -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("dwtgbot.cleanup")

    errors = settings.validate_runtime(require_storage=True, require_tools=False)
    if errors:
        for err in errors:
            log.error("cleanup_startup_check_failed", reason=err)
        return 2

    composition = build_api(settings)
    media_cache_repo = SqlAlchemyMediaCacheRepository(composition.core.sessionmaker)

    stop = asyncio.Event()

    def _stop(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _stop)

    log.info("cleanup_started", interval_sec=settings.CLEANUP_INTERVAL_SECONDS)
    try:
        while not stop.is_set():
            try:
                await _run_cycle(composition, media_cache_repo)
            except Exception:  # pragma: no cover
                log.exception("cleanup_cycle_error")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.CLEANUP_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue
    finally:
        await composition.aclose()
        log.info("cleanup_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
