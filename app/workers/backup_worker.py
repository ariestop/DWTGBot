"""
Backup worker — schedules invocations of ``deploy/scripts/backup.sh``.

We keep the actual backup logic (pg_dump, retention) in a shell script so
it can also be run manually or by an external scheduler. This worker just
provides a long-lived containerized loop for environments without cron.

Interval is fixed daily by default; override with BACKUP_INTERVAL_SECONDS env.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

from app.config import get_settings
from app.logging_config import configure_logging, get_logger

DEFAULT_INTERVAL = 24 * 60 * 60  # daily


async def _run_backup() -> int:
    log = get_logger("dwtgbot.backup")
    script = os.environ.get("BACKUP_SCRIPT", "/app/deploy/scripts/backup.sh")
    proc = await asyncio.create_subprocess_exec(
        "bash",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = (out or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode == 0:
        log.info("backup_done", output_tail=text[-2000:])
    else:
        log.error("backup_failed", code=proc.returncode, output_tail=text[-2000:])
    return proc.returncode or 0


async def _amain() -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("dwtgbot.backup")

    interval = int(os.environ.get("BACKUP_INTERVAL_SECONDS", DEFAULT_INTERVAL))
    stop = asyncio.Event()

    def _stop(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _stop)

    log.info("backup_worker_started", interval_sec=interval)
    while not stop.is_set():
        await _run_backup()
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    log.info("backup_worker_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
