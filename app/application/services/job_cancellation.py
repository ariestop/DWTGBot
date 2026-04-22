"""
Cross-process cancellation signal for in-flight download jobs.

Used by the bot's ``cancel_job`` callback to tell the worker "stop as
soon as convenient" — we can't actually interrupt the ``yt-dlp`` or
``ffmpeg`` subprocess mid-stream without significant engineering, so
the signal is cooperative: the worker checks the flag at phase
boundaries (start of ``_run``, before DOWNLOADING, before PROCESSING)
and raises :class:`app.exceptions.JobCancelledError` when it sees one.

Key shape: ``cancel:{job_id}`` string with TTL
``Settings.CANCEL_FLAG_TTL_SEC`` (default 30 min). TTL matters because
a worker that never picks up a cancelled PENDING job would otherwise
leave a stale ``1`` in Redis forever.

The port lives in the application layer (protocol only); the concrete
Redis implementation is in
``app.infrastructure.cache.redis_job_cancellation``. This keeps the
worker's use-case testable without a live Redis.
"""

from __future__ import annotations

from typing import Protocol


class JobCancellationStore(Protocol):
    """Cooperative cancellation flag storage."""

    async def request(self, *, job_id: int) -> None:
        """Mark ``job_id`` as cancelled. Idempotent."""

    async def is_cancelled(self, *, job_id: int) -> bool:
        """Return ``True`` iff :meth:`request` was called for ``job_id``
        within the TTL window."""

    async def clear(self, *, job_id: int) -> None:
        """Drop the flag (e.g. after the worker has acknowledged it).

        Best-effort: a lingering flag only affects the specific job_id
        reuse path — which does not happen in practice since job_ids
        are DB auto-increment.
        """
