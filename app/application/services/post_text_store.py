"""
Post-text side-channel storage (ADR-0010 §2.3).

Holds the source post's description / caption between "job created" and
"user taps the button". Deliberately Redis-only — the text is ephemeral
UX state, not domain data; it does not need to survive a Redis restart
(see Risk R6 in docs/tasks/archive/instant-download-ux.md) and it MUST NOT get
pinned to Postgres where deletion would be a Herculean retention task.

Key shape: ``post_text:{job_id}`` string, TTL
``Settings.POST_TEXT_TTL_SEC`` (default 24h).

Port only — concrete impl lives in
``app.infrastructure.cache.redis_post_text_store``. Keeps the bot
handler testable against an in-memory fake.
"""

from __future__ import annotations

from typing import Protocol


class PostTextStore(Protocol):
    """Key-value writer + reader for source-post descriptions."""

    async def put(self, *, job_id: int, text: str) -> None:
        """Persist ``text`` under ``post_text:{job_id}`` with the
        configured TTL. Called by the auto-enqueue use case right
        after the job row lands in Postgres; swallows Redis errors
        internally (best-effort — a missed post-text is not a job
        failure)."""

    async def get(self, *, job_id: int) -> str | None:
        """Return the persisted text, or ``None`` when the key is
        absent (never written / TTL expired / Redis outage). Reader
        errors are swallowed to keep the cancel path simple: the
        handler renders the "больше недоступен" alert on ``None``."""

    async def exists(self, *, job_id: int) -> bool:
        """Fast membership check. Used by ``DeliveryService`` to
        decide whether to render the post-text button without pulling
        the full payload over the wire."""
