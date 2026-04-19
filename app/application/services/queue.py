"""Queue producer abstraction. Concrete implementation is arq-based."""

from __future__ import annotations

from typing import Protocol

from app.application.dto.jobs import WorkerJobPayload


class QueueProducer(Protocol):
    """Pushes download jobs onto the worker queue."""

    async def enqueue_download(self, payload: WorkerJobPayload) -> None: ...
