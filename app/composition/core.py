"""Infrastructure shared by every composition: engine, Redis, providers, metrics."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.services.job_metrics import JobMetrics, NoopJobMetrics
from app.application.services.providers import ProviderRegistry
from app.application.services.rate_limit_metrics import (
    NoopRateLimitMetrics,
    RateLimitMetrics,
)
from app.config import Settings
from app.infrastructure.cache.redis_circuit_breaker import RedisCircuitBreaker
from app.infrastructure.cache.redis_pool import build_redis
from app.infrastructure.db.session import build_engine, build_sessionmaker
from app.infrastructure.downloader.ytdlp_runner import YtDlpRunner
from app.infrastructure.providers.instagram import InstagramProvider
from app.infrastructure.providers.registry import DefaultProviderRegistry
from app.infrastructure.providers.youtube import YouTubeProvider
from app.infrastructure.storage.local_storage import LocalStorage


@dataclass(slots=True)
class CoreInfra:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    redis: Redis


def build_core(settings: Settings) -> CoreInfra:
    engine = build_engine(settings)
    sm = build_sessionmaker(engine)
    redis = build_redis(settings)
    return CoreInfra(settings=settings, engine=engine, sessionmaker=sm, redis=redis)


def build_provider_registry(
    settings: Settings,
    storage: LocalStorage,
    *,
    redis: Redis | None = None,
) -> ProviderRegistry:
    # L5 (audit fix): per-host circuit breaker shared across the fleet
    # via Redis. ``redis=None`` disables the breaker (tests / one-off
    # shells without Redis). In production both the bot and the
    # worker build the registry with a live Redis connection.
    breaker: RedisCircuitBreaker | None = None
    if settings.CB_ENABLED and redis is not None:
        breaker = RedisCircuitBreaker(
            redis,
            failure_threshold=settings.CB_FAILURE_THRESHOLD,
            window_seconds=settings.CB_WINDOW_SECONDS,
            cooldown_seconds=settings.CB_COOLDOWN_SECONDS,
        )
    ytdlp = YtDlpRunner(settings, breaker=breaker)
    yt = YouTubeProvider(settings=settings, ytdlp=ytdlp, storage=storage)
    ig = InstagramProvider(settings=settings, ytdlp=ytdlp, storage=storage)
    return DefaultProviderRegistry([yt, ig])


def build_metrics(
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
