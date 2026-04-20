"""Worker process entrypoint.

Runs arq using the project's WorkerSettings. Equivalent to:

    arq app.infrastructure.queue.worker_settings.WorkerSettings
"""

from __future__ import annotations

import sys


def main() -> None:
    import asyncio

    from arq.worker import run_worker

    from app.infrastructure.queue.worker_settings import WorkerSettings

    # Python 3.14 compat: arq<=0.26.1 still calls asyncio.get_event_loop()
    # in Worker.__init__ expecting the legacy "create one on demand" fallback
    # in the main thread. 3.14 removed that fallback entirely — the call
    # now raises RuntimeError("There is no current event loop in thread
    # 'MainThread'") and the worker crash-loops on startup. We install a
    # loop explicitly before handing off; arq reuses it via
    # loop.run_until_complete(...). Harmless on earlier Pythons.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    # arq's ``run_worker`` accepts either ``dict`` or a subclass of
    # ``WorkerSettingsBase``. Our ``WorkerSettings`` is a duck-typed bare
    # class (the convention encouraged by the arq docs); making it inherit
    # from ``WorkerSettingsBase`` would force re-declaring fields with
    # explicit defaults. The runtime contract is satisfied — silence mypy.
    run_worker(WorkerSettings)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
    sys.exit(0)
