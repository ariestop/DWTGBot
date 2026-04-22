"""
NoopProgressReporter — inert :class:`ProgressReporter` implementation.

Used in three situations:

1. Unit tests of use cases that depend on ``ProgressReporter`` but do
   not need the side-channel behaviour under test.
2. The ``INSTANT_DOWNLOAD_ENABLED=false`` branch: the feature flag
   rolls back the new UX, and the worker keeps running the classic
   picker-driven flow. Using a Noop keeps the use-case constructor
   signature flag-insensitive.
3. The foundation PR (PR 1 of the instant-download rollout):
   composition wires this implementation everywhere so production
   behaviour is literally unchanged until later PRs swap it for the
   Redis-backed reporter.

The class is intentionally a plain object: no Redis, no logging, no
metrics. Everything it does is "nothing, successfully".
"""

from __future__ import annotations

from app.application.ports.progress_reporter import ProgressReporter
from app.domain.enums import ProgressStage


class NoopProgressReporter(ProgressReporter):
    """Drop-all implementation of :class:`ProgressReporter`.

    Every call is an immediate ``return None``. The class is stateless
    and safe to share across the whole process.
    """

    __slots__ = ()

    async def start(
        self,
        *,
        job_id: int,
        chat_id: int,
        message_id: int,
        thumbnail_url: str | None,
    ) -> None:
        return None

    async def update(
        self,
        *,
        job_id: int,
        percent: float,
        stage: ProgressStage,
    ) -> None:
        return None

    async def finish(self, *, job_id: int) -> None:
        return None

    async def fail(self, *, job_id: int, reason: str) -> None:
        return None

    async def cancel(self, *, job_id: int) -> None:
        return None
