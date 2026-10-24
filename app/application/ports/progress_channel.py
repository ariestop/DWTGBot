"""Redis layout of the live-progress side channel (ADR-0010 §2.2).

Shared contract between the only writer (``RedisProgressReporter`` in the
worker) and the only reader (``ProgressUpdater`` in the bot, through the
``ProgressFeed`` port below; ``RedisProgressFeed`` implements it). Changing a
name here is a wire-format change: deploy worker and bot together.

* ``progress:events``        — PUB/SUB channel carrying ``str(job_id)``.
* ``progress:{job_id}``      — hash with the latest stage / percent.
* ``progress_meta:{job_id}`` — hash with chat_id, message_id, started_at.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

EVENTS_CHANNEL = "progress:events"
PROGRESS_KEY_PREFIX = "progress:"
PROGRESS_META_KEY_PREFIX = "progress_meta:"


def progress_key(job_id: int) -> str:
    return f"{PROGRESS_KEY_PREFIX}{job_id}"


def progress_meta_key(job_id: int) -> str:
    return f"{PROGRESS_META_KEY_PREFIX}{job_id}"


class ProgressFeedError(Exception):
    """The side channel is unreachable (Redis down, connection reset)."""


class ProgressSubscription(Protocol):
    """One subscription to ``progress:events``."""

    async def next_job_id(self, *, wait_s: float) -> int | None:
        """The job id of the next event, or ``None`` when nothing usable
        arrived within ``wait_s`` seconds. Raises :class:`ProgressFeedError`."""

    async def close(self) -> None: ...


class ProgressFeed(Protocol):
    """Read side of the channel, used by the bot's ``ProgressUpdater``.

    Hashes come back as ``str -> str``; a missing key reads as ``{}``.
    Every method raises :class:`ProgressFeedError` when Redis fails.
    """

    async def read_progress(self, job_id: int) -> dict[str, str]: ...

    async def read_meta(self, job_id: int) -> dict[str, str]: ...

    def placeholders(self) -> AsyncIterator[tuple[int, dict[str, str]]]:
        """``(job_id, meta)`` of every live ``progress_meta:*`` hash.

        A hash that cannot be read is logged and skipped; a failed scan
        raises :class:`ProgressFeedError`.
        """
        ...

    async def subscribe(self) -> ProgressSubscription: ...
