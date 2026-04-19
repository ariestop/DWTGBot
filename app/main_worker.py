"""Worker process entrypoint.

Runs arq using the project's WorkerSettings. Equivalent to:

    arq app.infrastructure.queue.worker_settings.WorkerSettings
"""

from __future__ import annotations

import sys


def main() -> None:
    from arq.worker import run_worker

    from app.infrastructure.queue.worker_settings import WorkerSettings

    run_worker(WorkerSettings)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
    sys.exit(0)
