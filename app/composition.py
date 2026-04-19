"""
Composition root.

The single place where concrete infrastructure is wired into the
application. Each entrypoint (bot / api / worker) calls one of the
``build_*`` helpers, gets back a typed bundle, and uses it.
"""

from __future__ import annotations

from dataclasses import dataclass

from arq.connections import ArqRedis
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.services.delivery_service import DeliveryService
from app.application.services.job_metrics import JobMetrics, NoopJobMetrics
from app.application.services.providers import ProviderRegistry
from app.application.services.queue import QueueProducer
from app.application.services.rate_limit import InMemoryNoticeThrottle, NoticeThrottle
from app.application.services.rate_limit_gate import NoopRateLimitGate, RateLimitGate
from app.application.services.rate_limit_metrics import (
    NoopRateLimitMetrics,
    RateLimitMetrics,
)
from app.application.services.request_state_store import RequestStateStore
from app.application.services.temp_link_service import TempLinkService
from app.application.use_cases.analyze_link import AnalyzeLinkUseCase
from app.application.use_cases.enqueue_download import EnqueueDownloadUseCase
from app.application.use_cases.process_download import ProcessDownloadUseCase
from app.bot.container import BotContainer
from app.config import Settings
from app.infrastructure.cache.redis_pool import build_redis
from app.infrastructure.cache.redis_rate_limit_gate import RedisRateLimitGate
from app.infrastructure.cache.redis_state_store import RedisRequestStateStore
from app.infrastructure.db.repositories.jobs_repo_impl import SqlAlchemyJobsRepository
from app.infrastructure.db.repositories.temp_links_repo_impl import SqlAlchemyTempLinksRepository
from app.infrastructure.db.session import build_engine, build_sessionmaker
from app.infrastructure.downloader.ytdlp_runner import YtDlpRunner
from app.infrastructure.providers.instagram import InstagramProvider
from app.infrastructure.providers.registry import DefaultProviderRegistry
from app.infrastructure.providers.youtube import YouTubeProvider
from app.infrastructure.queue.arq_pool import build_arq_pool
from app.infrastructure.queue.producer import ArqQueueProducer
from app.infrastructure.storage.local_storage import LocalStorage
from app.infrastructure.telegram.sender import TelegramSender

# ---------------- Bundles ----------------


@dataclass(slots=True)
class CoreInfra:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis


@dataclass(slots=True)
class BotComposition:
    core: CoreInfra
    container: BotContainer
    arq_pool: ArqRedis
    # Optional /metrics server. Created only when METRICS_ENABLED=true.
    # The bot entrypoint owns the start/stop calls (main_bot.py).
    metrics_server: object | None = None
    # Optional queue-depth sampler. Same lifecycle as metrics_server —
    # the bot entrypoint starts/stops it. ADR-0007 §2.6.
    queue_sampler: object | None = None

    async def aclose(self) -> None:
        if self.queue_sampler is not None:
            stop = getattr(self.queue_sampler, "stop", None)
            if stop is not None:
                await stop()
        if self.metrics_server is not None:
            stop = getattr(self.metrics_server, "stop", None)
            if stop is not None:
                await stop()
        await self.arq_pool.close()
        # ``aclose`` is the redis-py 5.x async-shutdown method; types-redis
        # 4.6 stubs predate it, so mypy needs a narrow ignore here.
        await self.core.redis.aclose()  # type: ignore[attr-defined]
        await self.core.engine.dispose()


@dataclass(slots=True)
class WorkerComposition:
    core: CoreInfra
    use_case: ProcessDownloadUseCase
    sender: TelegramSender
    storage: LocalStorage
    # ADR-0007: worker process owns its own /metrics server + JobMetrics
    # sink. Lifecycle is wired through the arq on_startup / on_shutdown
    # hooks in ``app/infrastructure/queue/worker_settings.py``.
    job_metrics: JobMetrics
    metrics_server: object | None = None

    async def aclose(self) -> None:
        if self.metrics_server is not None:
            stop = getattr(self.metrics_server, "stop", None)
            if stop is not None:
                await stop()
        await self.sender.shutdown()
        await self.core.redis.aclose()  # type: ignore[attr-defined]
        await self.core.engine.dispose()


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


# ---------------- Builders ----------------


def _build_core(settings: Settings) -> CoreInfra:
    engine = build_engine(settings)
    sm = build_sessionmaker(engine)
    redis = build_redis(settings)
    return CoreInfra(settings=settings, engine=engine, sessionmaker=sm, redis=redis)


def _build_provider_registry(settings: Settings, storage: LocalStorage) -> ProviderRegistry:
    ytdlp = YtDlpRunner(settings)
    yt = YouTubeProvider(settings=settings, ytdlp=ytdlp, storage=storage)
    ig = InstagramProvider(settings=settings, ytdlp=ytdlp, storage=storage)
    return DefaultProviderRegistry([yt, ig])


async def build_bot(settings: Settings) -> BotComposition:
    core = _build_core(settings)
    storage = LocalStorage(settings)  # bot doesn't need disk; harmless to construct
    providers = _build_provider_registry(settings, storage)

    state_store: RequestStateStore = RedisRequestStateStore(core.redis)

    arq_pool = await build_arq_pool(settings)
    queue: QueueProducer = ArqQueueProducer(arq_pool)

    jobs_repo = SqlAlchemyJobsRepository(core.sessionmaker)

    analyze = AnalyzeLinkUseCase(
        providers=providers,
        state_store=state_store,
        request_ttl_seconds=settings.MEDIA_CACHE_TTL_SECONDS,
    )

    rl_metrics, job_metrics, metrics_server = _build_metrics(settings)

    enqueue = EnqueueDownloadUseCase(
        jobs_repo=jobs_repo,
        queue=queue,
        max_concurrent_per_user=settings.MAX_CONCURRENT_JOBS_PER_USER,
        metrics=job_metrics,
    )

    rate_limit_gate: RateLimitGate
    if settings.RL_ENABLED:
        rate_limit_gate = RedisRateLimitGate(
            core.redis,
            fail_open=settings.RL_FAIL_OPEN,
            metrics=rl_metrics,
        )
    else:
        rate_limit_gate = NoopRateLimitGate()
    notice_throttle: NoticeThrottle = InMemoryNoticeThrottle()

    container = BotContainer(
        settings=settings,
        analyze_link=analyze,
        enqueue_download=enqueue,
        request_state=state_store,
        rate_limit_gate=rate_limit_gate,
        notice_throttle=notice_throttle,
        metrics=rl_metrics,
        job_metrics=job_metrics,
    )

    queue_sampler = _build_queue_sampler(settings, pool=arq_pool, metrics=job_metrics)

    return BotComposition(
        core=core,
        container=container,
        arq_pool=arq_pool,
        metrics_server=metrics_server,
        queue_sampler=queue_sampler,
    )


def _build_metrics(
    settings: Settings,
) -> tuple[RateLimitMetrics, JobMetrics, object | None]:
    """Wire both metric sinks + optional /metrics HTTP server.

    Returns a triple ``(rate_limit_metrics, job_metrics, server)`` so a
    single registry serves both metric families on one /metrics
    endpoint per process. Imports of ``prometheus_client``/``uvicorn``
    are deferred — see ``ADR-0006`` §3 for the lazy-import rationale,
    extended here for ``ADR-0007``.
    """
    if not settings.METRICS_ENABLED:
        return NoopRateLimitMetrics(), NoopJobMetrics(), None

    # Lazy imports: prometheus-client / uvicorn are required only when
    # METRICS_ENABLED=true. Keeping them out of the top-level import set
    # lets a slimmed-down deploy (or tests) skip the dependency entirely.
    from prometheus_client import CollectorRegistry

    from app.infrastructure.metrics.prometheus_job_metrics import (
        PrometheusJobMetrics,
    )
    from app.infrastructure.metrics.prometheus_metrics import (
        PrometheusRateLimitMetrics,
    )
    from app.infrastructure.metrics.server import MetricsServer

    registry = CollectorRegistry()
    rl_metrics = PrometheusRateLimitMetrics(
        registry=registry,
        user_id_label_enabled=settings.METRICS_USER_ID_LABEL,
    )
    job_metrics = PrometheusJobMetrics(registry=registry)
    server = MetricsServer(
        registry=registry,
        host=settings.METRICS_BIND_HOST,
        port=settings.METRICS_PORT,
    )
    return rl_metrics, job_metrics, server


def _build_queue_sampler(
    settings: Settings, *, pool: ArqRedis, metrics: JobMetrics
) -> object | None:
    """Return a ``QueueDepthSampler`` instance or ``None`` when disabled.

    Lazy-imported for the same reason as ``_build_metrics``: keeps
    deploys with ``METRICS_ENABLED=false`` from pulling in
    ``prometheus_client``-adjacent code paths.
    """
    if not settings.METRICS_ENABLED:
        return None

    from app.infrastructure.metrics.queue_depth_sampler import (
        QueueDepthSampler,
    )
    from app.infrastructure.queue.arq_pool import WORKER_QUEUE_NAME

    return QueueDepthSampler(
        pool=pool,
        queue_name=WORKER_QUEUE_NAME,
        metrics=metrics,
        interval_seconds=settings.METRICS_QUEUE_SAMPLE_INTERVAL_S,
    )


def build_worker(settings: Settings) -> WorkerComposition:
    core = _build_core(settings)
    storage = LocalStorage(settings)
    storage.init()

    providers = _build_provider_registry(settings, storage)
    jobs_repo = SqlAlchemyJobsRepository(core.sessionmaker)
    temp_links_repo = SqlAlchemyTempLinksRepository(core.sessionmaker)

    sender = TelegramSender(settings)
    temp_link_service = TempLinkService(settings=settings, repo=temp_links_repo)
    delivery = DeliveryService(
        settings=settings,
        sender=sender,
        storage=storage,
        temp_links=temp_link_service,
    )

    # ADR-0007: worker has its own /metrics + JobMetrics sink.
    _, job_metrics, metrics_server = _build_metrics(settings)
    if settings.METRICS_ENABLED:
        # Concurrency is invariant for the lifetime of the process —
        # set once. Used as the denominator in the B4 saturation query.
        job_metrics.set_worker_concurrency(concurrency=settings.WORKER_CONCURRENCY)

    use_case = ProcessDownloadUseCase(
        jobs_repo=jobs_repo,
        providers=providers,
        storage=storage,
        delivery=delivery,
        sender=sender,
        settings=settings,
        metrics=job_metrics,
    )
    return WorkerComposition(
        core=core,
        use_case=use_case,
        sender=sender,
        storage=storage,
        job_metrics=job_metrics,
        metrics_server=metrics_server,
    )


async def build_api(settings: Settings) -> ApiComposition:
    core = _build_core(settings)
    storage = LocalStorage(settings)
    storage.init()
    temp_links_repo = SqlAlchemyTempLinksRepository(core.sessionmaker)

    # ADR-0007: api owns its own /metrics + JobMetrics sink. The
    # downloads router increments ``temp_link_serves_total`` on every
    # return path.
    _, job_metrics, metrics_server = _build_metrics(settings)

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
