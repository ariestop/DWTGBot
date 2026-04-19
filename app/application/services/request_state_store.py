"""
Short-lived per-request state shared between bot turns.

When a user sends a URL, the bot:
  1. analyzes the link,
  2. stashes the resulting AnalyzedMedia under a short request_id,
  3. shows inline buttons whose callback_data carries that request_id.

When the user clicks a button some seconds/minutes later we look the state
back up. State has a TTL so stale callbacks are handled gracefully.

Backed by Redis in production; an in-memory implementation is used in tests.
"""

from __future__ import annotations

from typing import Protocol

from app.application.dto.media import AnalyzedMedia


class RequestStateStore(Protocol):
    async def save(self, request_id: str, payload: AnalyzedMedia, ttl_seconds: int) -> None: ...

    async def load(self, request_id: str) -> AnalyzedMedia | None: ...

    async def delete(self, request_id: str) -> None: ...
