"""
Composition root.

The single place where concrete infrastructure is wired into the
application. Each entrypoint (bot / api / worker / cleanup) calls one of
the ``build_*`` helpers, gets back a typed bundle, and uses it. One
module per process; shared pieces live in ``core``.
"""

from __future__ import annotations

from app.composition.api import ApiComposition, build_api
from app.composition.bot import BotComposition, build_bot
from app.composition.cleanup import CleanupComposition, build_cleanup
from app.composition.core import CoreInfra
from app.composition.worker import WorkerComposition, build_worker

__all__ = [
    "ApiComposition",
    "BotComposition",
    "CleanupComposition",
    "CoreInfra",
    "WorkerComposition",
    "build_api",
    "build_bot",
    "build_cleanup",
    "build_worker",
]
