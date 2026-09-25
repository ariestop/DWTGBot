"""Cleanup loop wiring (L13 audit fix)."""

from __future__ import annotations

from dataclasses import dataclass

from app.application.services.job_metrics import JobMetrics
from app.composition.core import CoreInfra, build_core, build_metrics
from app.config import Settings
from app.infrastructure.db.repositories.jobs_repo_impl import SqlAlchemyJobsRepository
from app.infrastructure.db.repositories.media_cache_repo_impl import (
    SqlAlchemyMediaCacheRepository,
)
from app.infrastructure.db.repositories.temp_links_repo_impl import SqlAlchemyTempLinksRepository
from app.infrastructure.storage.local_storage import LocalStorage


@dataclass(slots=True)
class CleanupComposition:
    """Minimal infra bundle for the cleanup worker (L13 audit fix).

    Historically ``cleanup_worker`` reused ``build_api`` to get
    ``temp_links_repo`` + ``storage`` plus dev-only API shims. The
    cleanup worker now owns only the pieces it actually touches, plus
    its own optional metrics sink/server for background-loop telemetry.

    This bundle carries only what a cleanup cycle touches — no arq
    pool, no dev-only enqueue shim.
    """

    core: CoreInfra
    temp_links_repo: SqlAlchemyTempLinksRepository
    storage: LocalStorage
    media_cache_repo: SqlAlchemyMediaCacheRepository
    jobs_repo: SqlAlchemyJobsRepository
    job_metrics: JobMetrics
    metrics_server: object | None = None

    async def aclose(self) -> None:
        if self.metrics_server is not None:
            stop = getattr(self.metrics_server, "stop", None)
            if stop is not None:
                await stop()
        await self.core.redis.aclose()  # type: ignore[attr-defined]
        await self.core.engine.dispose()


async def build_cleanup(settings: Settings) -> CleanupComposition:
    """Build the cleanup-worker composition (L13 audit fix).

    Unlike ``build_api`` this does NOT wire an arq pool or any dev-only
    API helpers. It does get its own metrics sink/server so background
    cleanup work can surface counters independently.
    """
    core = build_core(settings)
    storage = LocalStorage(settings)
    storage.init()
    temp_links_repo = SqlAlchemyTempLinksRepository(core.sessionmaker)
    media_cache_repo = SqlAlchemyMediaCacheRepository(core.sessionmaker)
    jobs_repo = SqlAlchemyJobsRepository(core.sessionmaker)
    _, job_metrics, metrics_server = build_metrics(settings)
    return CleanupComposition(
        core=core,
        temp_links_repo=temp_links_repo,
        storage=storage,
        media_cache_repo=media_cache_repo,
        jobs_repo=jobs_repo,
        job_metrics=job_metrics,
        metrics_server=metrics_server,
    )
