"""Worker process entrypoint.

Runs arq using the project's WorkerSettings. Equivalent to:

    arq app.infrastructure.queue.worker_settings.WorkerSettings
"""

from __future__ import annotations

import sys


def main() -> None:
    from arq.worker import run_worker

    from app.infrastructure.queue.worker_settings import WorkerSettings

    # arq's ``run_worker`` accepts either ``dict`` or a subclass of
    # ``WorkerSettingsBase``. Our ``WorkerSettings`` is a duck-typed bare
    # class (the convention encouraged by the arq docs); making it inherit
    # from ``WorkerSettingsBase`` would force re-declaring fields with
    # explicit defaults. The runtime contract is satisfied — silence mypy.
    run_worker(WorkerSettings)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
    sys.exit(0)
