"""arq-backed QueueProducer."""

from __future__ import annotations

from arq.connections import ArqRedis

from app.application.dto.jobs import WorkerJobPayload
from app.application.services.queue import QueueProducer

PROCESS_DOWNLOAD_TASK = "process_download_job"


class ArqQueueProducer(QueueProducer):
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue_download(self, payload: WorkerJobPayload) -> None:
        await self._pool.enqueue_job(
            PROCESS_DOWNLOAD_TASK,
            payload.job_id,
            payload.correlation_id,
            _job_id=f"job:{payload.job_id}",
        )
