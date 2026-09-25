"""
ProgressReporter port — application-layer abstraction for live download
progress reporting.

Problem
-------
During a long-running download + transcode + upload cycle the worker
wants to surface progress to the user. The worker MUST NOT talk to the
Telegram API for progress messages: ownership of Telegram-UI lives in
the bot process (ADR-0010 §2.2, Alternative 4.1). What the worker
*can* do is publish progress to a side-channel; the bot subscribes and
edits a placeholder message.

This module defines the port. Concrete implementations:

* ``app.infrastructure.cache.redis_progress_reporter.RedisProgressReporter``
  (added in PR 3 of the instant-download feature set) — writes to a
  Redis hash and publishes a pubsub event.
* :class:`NoopProgressReporter` (this module) — silent sink used in
  tests and when ``Settings.INSTANT_DOWNLOAD_ENABLED=false``. It lives
  next to the port because it has no I/O and use cases fall back to it.

Semantics
---------
* **Best-effort.** Reporter methods MUST NOT raise on publication
  failure; they log-and-continue. A dropped progress update is never a
  reason to fail a download.
* **Idempotent ``finish()`` / ``fail()``.** Multiple calls with the
  same ``job_id`` produce the same terminal state without errors.
* **Monotonic ``percent``.** Use cases guarantee a non-decreasing
  percent per ``job_id``; reporters MAY clamp defensive.

The ``Protocol`` form (vs. ABC) keeps the domain light and matches
existing application ports such as ``RequestStateStore``.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.enums import ProgressStage


class ProgressReporter(Protocol):
    """Side-channel writer for download-lifecycle progress."""

    async def start(
        self,
        *,
        job_id: int,
        chat_id: int,
        message_id: int,
        thumbnail_url: str | None,
    ) -> None:
        """
        Register a newly-created job with its placeholder message.

        Implementations typically persist
        ``{chat_id, message_id, started_at}`` under
        ``progress_meta:{job_id}`` so the bot's progress updater can
        recover the target message after a restart (ADR-0010 §2.2).

        ``thumbnail_url`` is the source-provided preview URL — stored
        for informational / debugging purposes; the actual photo is
        sent by the bot layer at the moment the placeholder message is
        created, before ``start()`` is called.
        """

    async def update(
        self,
        *,
        job_id: int,
        percent: float,
        stage: ProgressStage,
    ) -> None:
        """
        Publish an intermediate progress sample.

        ``percent`` is a float in ``[0.0, 100.0]``; caller ensures
        monotonicity within a job. Reporters MAY debounce
        (min interval / min delta) before actually publishing, but the
        call itself MUST return promptly — this is hot-path in
        ``yt-dlp`` progress hooks.
        """

    async def finish(self, *, job_id: int) -> None:
        """
        Publish the terminal success state for a job.

        Consumer (bot's progress updater) deletes the placeholder
        message and stops watching the job. Idempotent — callable more
        than once with no side-effects on the second call.
        """

    async def fail(self, *, job_id: int, reason: str) -> None:
        """
        Publish the terminal failure state for a job.

        ``reason`` is a short human-readable string suitable for inline
        display. Secrets and stack traces MUST NOT appear here — they
        belong in the logs, not in user-visible captions. Idempotent.
        """

    async def cancel(self, *, job_id: int) -> None:
        """
        Publish the terminal cancellation state for a job.

        Distinct from :meth:`fail` so the updater can render "Отменено"
        instead of an error message before deleting the placeholder.
        Idempotent. Called by the bot's ``cancel_job`` handler the
        moment the user taps the cancel button — the worker
        independently raises :class:`JobCancelledError` at the next
        phase boundary and reconciles the job row.
        """


class NoopProgressReporter(ProgressReporter):
    """Drop-all implementation: every call returns immediately.

    Stateless and safe to share across the whole process.
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
