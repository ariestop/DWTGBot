"""Redis layout of the live-progress side channel (ADR-0010 §2.2).

Shared contract between the only writer (``RedisProgressReporter`` in the
worker) and the only reader (``ProgressUpdater`` in the bot). Changing a
name here is a wire-format change: deploy worker and bot together.

* ``progress:events``        — PUB/SUB channel carrying ``str(job_id)``.
* ``progress:{job_id}``      — hash with the latest stage / percent.
* ``progress_meta:{job_id}`` — hash with chat_id, message_id, started_at.
"""

from __future__ import annotations

EVENTS_CHANNEL = "progress:events"
PROGRESS_KEY_PREFIX = "progress:"
PROGRESS_META_KEY_PREFIX = "progress_meta:"


def progress_key(job_id: int) -> str:
    return f"{PROGRESS_KEY_PREFIX}{job_id}"


def progress_meta_key(job_id: int) -> str:
    return f"{PROGRESS_META_KEY_PREFIX}{job_id}"
