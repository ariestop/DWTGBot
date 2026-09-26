"""arq worker wiring (ProcessDownloadUseCase, delivery, progress writer)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.application.ports.progress_reporter import NoopProgressReporter, ProgressReporter
from app.application.services.delivery_service import DeliveryService
from app.application.services.job_cancellation import JobCancellationStore
from app.application.services.job_metrics import JobMetrics
from app.application.services.post_text_store import PostTextStore
from app.application.services.temp_link_service import TempLinkService
from app.application.use_cases.process_download import ProcessDownloadUseCase
from app.composition.core import CoreInfra, build_core, build_metrics, build_provider_registry
from app.config import Settings
from app.infrastructure.cache.redis_job_cancellation import RedisJobCancellationStore
from app.infrastructure.cache.redis_post_text_store import RedisPostTextStore
from app.infrastructure.cache.redis_progress_reporter import RedisProgressReporter
from app.infrastructure.db.repositories.jobs_repo_impl import SqlAlchemyJobsRepository
from app.infrastructure.db.repositories.media_cache_repo_impl import (
    SqlAlchemyMediaCacheRepository,
)
from app.infrastructure.db.repositories.temp_links_repo_impl import SqlAlchemyTempLinksRepository
from app.infrastructure.storage.local_storage import LocalStorage
from app.infrastructure.telegram.sender import TelegramSender


@dataclass(slots=True)
class WorkerComposition:
    core: CoreInfra
    jobs_repo: SqlAlchemyJobsRepository
    use_case: ProcessDownloadUseCase
    sender: TelegramSender
    storage: LocalStorage
    # ADR-0007: worker process owns its own /metrics server + JobMetrics
    # sink. Lifecycle is wired through the arq on_startup / on_shutdown
    # hooks in ``app/infrastructure/queue/worker_settings.py``.
    job_metrics: JobMetrics
    # ADR-0010 §2.2: see ``BotComposition.progress_reporter``. The
    # worker holds the same port so ``ProcessDownloadUseCase`` (in a
    # later PR) receives it via DI rather than reaching into globals.
    progress_reporter: ProgressReporter = field(default_factory=NoopProgressReporter)
    metrics_server: object | None = None

    async def aclose(self) -> None:
        if self.metrics_server is not None:
            stop = getattr(self.metrics_server, "stop", None)
            if stop is not None:
                await stop()
        # Let the reporter release its sync Redis client (if any).
        # ProgressReporter is a Protocol, concrete implementations are
        # free to add extra teardown hooks; duck-type via hasattr so we
        # do not force every impl to wear the sync-bridge responsibility.
        close_sync = getattr(self.progress_reporter, "close_sync_client", None)
        if close_sync is not None:
            close_sync()
        await self.sender.shutdown()
        await self.core.redis.aclose()  # type: ignore[attr-defined]
        await self.core.engine.dispose()


def build_worker(settings: Settings) -> WorkerComposition:
    core = build_core(settings)
    storage = LocalStorage(settings)
    storage.init()

    providers = build_provider_registry(settings, storage, redis=core.redis)
    jobs_repo = SqlAlchemyJobsRepository(core.sessionmaker)
    temp_links_repo = SqlAlchemyTempLinksRepository(core.sessionmaker)

    sender = TelegramSender(settings)
    temp_link_service = TempLinkService(settings=settings, repo=temp_links_repo)
    # ADR-0010 §2.3: DeliveryService renders the post-text button
    # iff the side-channel key is present at delivery time. Separate
    # instance from the bot's — both share Redis namespace.
    post_text_store: PostTextStore = RedisPostTextStore(
        redis=core.redis,
        settings=settings,
    )
    delivery = DeliveryService(
        settings=settings,
        sender=sender,
        storage=storage,
        temp_links=temp_link_service,
        post_text_store=post_text_store,
    )

    # ADR-0007: worker has its own /metrics + JobMetrics sink.
    _, job_metrics, metrics_server = build_metrics(settings)
    if settings.METRICS_ENABLED:
        # Concurrency is invariant for the lifetime of the process —
        # set once. Used as the denominator in the B4 saturation query.
        job_metrics.set_worker_concurrency(concurrency=settings.WORKER_CONCURRENCY)

    # ADR-0010 rollback contract: when instant-download is disabled the
    # worker must stop writing progress side-channel events entirely.
    # The bot falls back to the legacy picker UX, so emitting
    # ``progress:*`` updates would only create orphaned Redis keys.
    progress_reporter: ProgressReporter
    if settings.INSTANT_DOWNLOAD_ENABLED:
        progress_reporter = RedisProgressReporter(
            redis=core.redis,
            redis_url=settings.redis_url,
            settings=settings,
        )
    else:
        progress_reporter = NoopProgressReporter()
    cancellation: JobCancellationStore = RedisJobCancellationStore(
        redis=core.redis,
        settings=settings,
    )

    use_case = ProcessDownloadUseCase(
        jobs_repo=jobs_repo,
        providers=providers,
        storage=storage,
        delivery=delivery,
        sender=sender,
        settings=settings,
        metrics=job_metrics,
        progress_reporter=progress_reporter,
        cancellation=cancellation,
        media_cache=SqlAlchemyMediaCacheRepository(core.sessionmaker),
    )
    return WorkerComposition(
        core=core,
        jobs_repo=jobs_repo,
        use_case=use_case,
        sender=sender,
        storage=storage,
        job_metrics=job_metrics,
        progress_reporter=progress_reporter,
        metrics_server=metrics_server,
    )
