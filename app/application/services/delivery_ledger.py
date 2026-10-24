"""What a job already delivered, so a retry does not deliver it twice.

``ProcessDownloadUseCase`` delivers first and writes DONE second. When
anything fails in between (a database blip on that write, a crash), arq
retries the job, and the retry used to download and send the file
again: the user got it twice. The use case now records the delivery
right after it succeeds; a retry that finds the record only finishes
the bookkeeping.

The port lives here (protocol only); the Redis implementation is in
``app.infrastructure.cache.redis_delivery_ledger``. Both operations are
best-effort: an outage loses the protection, never the download.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.enums import DeliveryMethod


@dataclass(frozen=True, slots=True)
class DeliveredRecord:
    """Everything ``mark_done`` needs, captured at delivery time."""

    method: DeliveryMethod
    public_url: str | None
    primary_telegram_file_id: str | None
    delivered_path: str
    file_size: int
    mime_type: str | None
    title: str
    media_id: str
    selected_format: str | None


class DeliveryLedger(Protocol):
    async def record(self, *, job_id: int, delivered: DeliveredRecord) -> None:
        """Remember that ``job_id`` was delivered. Never raises."""

    async def get(self, *, job_id: int) -> DeliveredRecord | None:
        """The record for ``job_id``, or ``None`` (also on an outage)."""
