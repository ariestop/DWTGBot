"""FastAPI process wiring (temp-link serving, dev-only enqueue)."""

from __future__ import annotations

from dataclasses import dataclass

from arq.connections import ArqRedis

from app.application.services.job_metrics import JobMetrics
from app.application.services.queue import QueueProducer
from app.application.use_cases.enqueue_download import EnqueueDownloadUseCase
from app.composition.core import CoreInfra, build_core, build_metrics
from app.config import Settings
from app.infrastructure.db.repositories.jobs_repo_impl import SqlAlchemyJobsRepository
from app.infrastructure.db.repositories.temp_links_repo_impl import SqlAlchemyTempLinksRepository
from app.infrastructure.queue.arq_pool import build_arq_pool
from app.infrastructure.queue.producer import ArqQueueProducer
from app.infrastructure.storage.local_storage import LocalStorage


@dataclass(slots=True)
class ApiComposition:
    core: CoreInfra
    temp_links_repo: SqlAlchemyTempLinksRepository
    storage: LocalStorage
    # ADR-0007: api process owns its own /metrics server. The downloads
    # router pulls the sink off the request-state ``composition``.
    job_metrics: JobMetrics
    metrics_server: object | None = None
    # Lazily-built dev helpers — only populated when a dev/CI feature is
    # enabled (e.g. INTERNAL_TEST_TOKEN). Stay None in production.
    arq_pool: ArqRedis | None = None
    enqueue_download: EnqueueDownloadUseCase | None = None

    async def aclose(self) -> None:
        if self.metrics_server is not None:
            stop = getattr(self.metrics_server, "stop", None)
            if stop is not None:
                await stop()
        if self.arq_pool is not None:
            await self.arq_pool.close()
        await self.core.redis.aclose()  # type: ignore[attr-defined]
        await self.core.engine.dispose()


async def build_api(settings: Settings) -> ApiComposition:
    core = build_core(settings)
    storage = LocalStorage(settings)
    storage.init()
    temp_links_repo = SqlAlchemyTempLinksRepository(core.sessionmaker)

    # ADR-0007: api owns its own /metrics + JobMetrics sink. The
    # downloads router increments ``temp_link_serves_total`` on every
    # return path.
    _, job_metrics, metrics_server = build_metrics(settings)

    arq_pool: ArqRedis | None = None
    enqueue: EnqueueDownloadUseCase | None = None
    # Dev-only helpers — gated by an explicit env var. Production startup
    # rejects a non-empty INTERNAL_TEST_TOKEN (see Settings.validate_runtime).
    if settings.INTERNAL_TEST_TOKEN:
        arq_pool = await build_arq_pool(settings)
        queue: QueueProducer = ArqQueueProducer(arq_pool)
        jobs_repo = SqlAlchemyJobsRepository(core.sessionmaker)
        enqueue = EnqueueDownloadUseCase(
            jobs_repo=jobs_repo,
            queue=queue,
            max_concurrent_per_user=settings.MAX_CONCURRENT_JOBS_PER_USER,
            metrics=job_metrics,
        )

    return ApiComposition(
        core=core,
        temp_links_repo=temp_links_repo,
        storage=storage,
        job_metrics=job_metrics,
        metrics_server=metrics_server,
        arq_pool=arq_pool,
        enqueue_download=enqueue,
    )
