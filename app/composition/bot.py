"""Bot process wiring (python-telegram-bot handlers, progress updater)."""

from __future__ import annotations

from dataclasses import dataclass, field

from arq.connections import ArqRedis

from app.application.ports.progress_reporter import NoopProgressReporter, ProgressReporter
from app.application.services.job_cancellation import JobCancellationStore
from app.application.services.job_metrics import JobMetrics
from app.application.services.post_text_store import PostTextStore
from app.application.services.queue import QueueProducer
from app.application.services.rate_limit import NoticeThrottle
from app.application.services.rate_limit_gate import NoopRateLimitGate, RateLimitGate
from app.application.services.request_state_store import RequestStateStore
from app.application.use_cases.analyze_link import AnalyzeLinkUseCase
from app.application.use_cases.auto_enqueue_download import AutoEnqueueDownloadUseCase
from app.application.use_cases.enqueue_download import EnqueueDownloadUseCase
from app.bot.container import BotContainer
from app.bot.services.progress_updater import ProgressUpdater
from app.composition.core import CoreInfra, build_core, build_metrics, build_provider_registry
from app.config import Settings
from app.infrastructure.cache.redis_job_cancellation import RedisJobCancellationStore
from app.infrastructure.cache.redis_notice_throttle import RedisNoticeThrottle
from app.infrastructure.cache.redis_post_text_store import RedisPostTextStore
from app.infrastructure.cache.redis_progress_reporter import RedisProgressReporter
from app.infrastructure.cache.redis_rate_limit_gate import RedisRateLimitGate
from app.infrastructure.cache.redis_state_store import RedisRequestStateStore
from app.infrastructure.db.repositories.jobs_repo_impl import SqlAlchemyJobsRepository
from app.infrastructure.db.repositories.media_cache_repo_impl import (
    SqlAlchemyMediaCacheRepository,
)
from app.infrastructure.queue.arq_pool import build_arq_pool
from app.infrastructure.queue.producer import ArqQueueProducer
from app.infrastructure.storage.local_storage import LocalStorage


@dataclass(slots=True)
class BotComposition:
    core: CoreInfra
    container: BotContainer
    arq_pool: ArqRedis
    # ADR-0010 §2.2: side-channel progress writer (Noop unless wired).
    progress_reporter: ProgressReporter = field(default_factory=NoopProgressReporter)
    # ADR-0010 §2.2: side-channel consumer. Constructed
    # eagerly so the bot entrypoint can call ``.start(bot)`` after PTB
    # initialisation; None when ``PROGRESS_TTL_SEC <= 0`` is ever added
    # as a kill-switch (not exposed today).
    progress_updater: ProgressUpdater | None = None
    # Optional /metrics server. Created only when METRICS_ENABLED=true.
    # The bot entrypoint owns the start/stop calls (main_bot.py).
    metrics_server: object | None = None
    # Optional queue-depth sampler. Same lifecycle as metrics_server —
    # the bot entrypoint starts/stops it. ADR-0007 §2.6.
    queue_sampler: object | None = None

    async def aclose(self) -> None:
        if self.progress_updater is not None:
            await self.progress_updater.stop()
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


async def build_bot(settings: Settings) -> BotComposition:
    core = build_core(settings)
    storage = LocalStorage(settings)  # bot doesn't need disk; harmless to construct
    providers = build_provider_registry(settings, storage, redis=core.redis)

    state_store: RequestStateStore = RedisRequestStateStore(core.redis)

    arq_pool = await build_arq_pool(settings)
    queue: QueueProducer = ArqQueueProducer(arq_pool)

    jobs_repo = SqlAlchemyJobsRepository(core.sessionmaker)
    media_cache_repo = SqlAlchemyMediaCacheRepository(core.sessionmaker)

    analyze = AnalyzeLinkUseCase(
        providers=providers,
        state_store=state_store,
        request_ttl_seconds=settings.MEDIA_CACHE_TTL_SECONDS,
        media_cache=media_cache_repo,
    )

    rl_metrics, job_metrics, metrics_server = build_metrics(settings)

    enqueue = EnqueueDownloadUseCase(
        jobs_repo=jobs_repo,
        queue=queue,
        max_concurrent_per_user=settings.MAX_CONCURRENT_JOBS_PER_USER,
        metrics=job_metrics,
    )

    # ADR-0010 §2.1: bot needs its own RedisProgressReporter to call
    # ``reporter.cancel`` from the cancel-button handler. The worker
    # builds a separate instance (different lifetime / sync-bridge);
    # both use the key layout in ``app.application.ports.progress_channel``.
    bot_progress_reporter: ProgressReporter = RedisProgressReporter(
        redis=core.redis,
        redis_url=settings.redis_url,
        settings=settings,
    )
    job_cancellation: JobCancellationStore = RedisJobCancellationStore(
        redis=core.redis,
        settings=settings,
    )
    # ADR-0010 §2.3: post-text side channel. Writer path is in the
    # auto-enqueue use case, reader path is the callback handler.
    # Shared instance so both keep TTL semantics in one place.
    post_text_store: PostTextStore = RedisPostTextStore(
        redis=core.redis,
        settings=settings,
    )

    auto_enqueue = AutoEnqueueDownloadUseCase(
        providers=providers,
        jobs_repo=jobs_repo,
        queue=queue,
        progress_reporter=bot_progress_reporter,
        post_text_store=post_text_store,
        max_concurrent_per_user=settings.MAX_CONCURRENT_JOBS_PER_USER,
        post_text_min_chars=settings.POST_TEXT_MIN_CHARS,
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
    # L4 (audit fix): cross-process throttle on "too many requests" /
    # cap-hit notices. Previously a per-process dict — would spam the
    # user once per replica behind a shared webhook.
    notice_throttle: NoticeThrottle = RedisNoticeThrottle(core.redis)

    container = BotContainer(
        settings=settings,
        analyze_link=analyze,
        enqueue_download=enqueue,
        auto_enqueue_download=auto_enqueue,
        request_state=state_store,
        rate_limit_gate=rate_limit_gate,
        notice_throttle=notice_throttle,
        metrics=rl_metrics,
        job_metrics=job_metrics,
        progress_reporter=bot_progress_reporter,
        job_cancellation=job_cancellation,
        post_text_store=post_text_store,
    )

    queue_sampler = _build_queue_sampler(settings, pool=arq_pool, metrics=job_metrics)

    # ADR-0010 §2.2: the bot is the sole *reader* of the side-channel.
    # Built unconditionally: with no producer events it simply stays idle.
    progress_updater = ProgressUpdater(settings=settings, redis=core.redis)

    return BotComposition(
        core=core,
        container=container,
        arq_pool=arq_pool,
        progress_reporter=bot_progress_reporter,
        progress_updater=progress_updater,
        metrics_server=metrics_server,
        queue_sampler=queue_sampler,
    )


def _build_queue_sampler(
    settings: Settings, *, pool: ArqRedis, metrics: JobMetrics
) -> object | None:
    """Return a ``QueueDepthSampler`` instance or ``None`` when disabled.

    Lazy-imported for the same reason as ``build_metrics``: keeps
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
