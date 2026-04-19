"""
Lightweight DI container for the bot process.

Holds use cases and shared services. Stored in
``Application.bot_data["container"]`` so handlers pick it up via
``context.bot_data["container"]``. Kept as a frozen dataclass to make
intent obvious — this is not a generic IoC framework.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.services.job_metrics import JobMetrics
from app.application.services.rate_limit import NoticeThrottle
from app.application.services.rate_limit_gate import RateLimitGate
from app.application.services.rate_limit_metrics import RateLimitMetrics
from app.application.services.request_state_store import RequestStateStore
from app.application.use_cases.analyze_link import AnalyzeLinkUseCase
from app.application.use_cases.enqueue_download import EnqueueDownloadUseCase
from app.config import Settings


@dataclass(frozen=True, slots=True)
class BotContainer:
    settings: Settings
    analyze_link: AnalyzeLinkUseCase
    enqueue_download: EnqueueDownloadUseCase
    request_state: RequestStateStore
    # Rate-limiting (docs/36-rate-limiting.md). The gate is a Noop when
    # ``RL_ENABLED=false`` so handlers can call ``evaluate`` unconditionally.
    rate_limit_gate: RateLimitGate
    notice_throttle: NoticeThrottle
    # Limiter metrics sink (docs/35-metrics-and-slo.md §7, ADR-0006).
    # Always present — Noop when METRICS_ENABLED=false — so handlers
    # never need a None-check.
    metrics: RateLimitMetrics
    # Job/queue/worker metrics sink (ADR-0007). Same Noop discipline:
    # the use case and the metrics middleware always have a real
    # callable, even when METRICS_ENABLED=false.
    job_metrics: JobMetrics


CONTAINER_KEY = "container"


def get_container(bot_data: dict) -> BotContainer:
    """Type-safe accessor for handlers."""
    container = bot_data.get(CONTAINER_KEY)
    if not isinstance(container, BotContainer):
        raise RuntimeError("Bot container is not initialized")
    return container
