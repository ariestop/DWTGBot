"""Application-layer metrics port for job/queue/worker observability.

Mirrors the shape of :mod:`app.application.services.rate_limit_metrics`
introduced by ``ADR-0006``: a thin ``Protocol`` so production code
depends on the *interface*, plus a ``Noop`` for tests and for runs
with ``METRICS_ENABLED=false``. The concrete Prometheus implementation
lives in :mod:`app.infrastructure.metrics.prometheus_job_metrics`
(``ADR-0007``).

Each method maps 1:1 to a metric in ``docs/35-metrics-and-slo.md`` §7.
Renaming a method requires a matching docs update and a search/replace
across both the bot-process and worker-process call sites — keep that
in mind before "harmonising" anything here.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.enums import JobStatus, Platform
from app.domain.observability import (
    FileSizeClass,
    HandlerOutcome,
    LatencyBucket,
    TempLinkServeResult,
)
from app.domain.reason_class import ReasonClass


class JobMetrics(Protocol):
    """Hot-path interface — implementations MUST be O(1).

    All increments are paired with a structured log event by the
    caller (P11). If you find yourself wanting a method that doesn't
    map to an SLO entry in ``docs/35-`` §3, stop and revisit the SLO
    catalogue first; ad-hoc metrics dilute the dashboard.
    """

    # ----- Bot-side ----------------------------------------------------

    def inc_bot_message_handled(
        self, *, outcome: HandlerOutcome, latency_bucket: LatencyBucket
    ) -> None:
        """``bot_message_handled_total{outcome, latency_bucket}`` (A1)."""

    def inc_job_created(self, *, platform: Platform) -> None:
        """``jobs_created_total{platform}`` (A2 numerator base)."""

    def set_queue_depth(self, *, depth: int) -> None:
        """``arq_queue_depth`` Gauge (B3).

        Set by the periodic sampler in
        :mod:`app.infrastructure.metrics.queue_depth_sampler`. Gauges
        are not idempotent across processes — only the bot process
        owns the arq pool, so only the bot calls this.
        """

    # ----- Worker-side -------------------------------------------------

    def inc_status_change(self, *, to: JobStatus, reason_class: ReasonClass) -> None:
        """``jobs_status_changed_total{to, reason_class}`` (A2/A3).

        Emitted at every ``mark_*`` site in
        :class:`~app.application.use_cases.process_download.ProcessDownloadUseCase`.
        Pairs with a ``job_status_changed`` log event.
        """

    def observe_job_duration(self, *, file_size_class: FileSizeClass, seconds: float) -> None:
        """``job_duration_seconds{file_size_class}`` Histogram (A4/A5).

        Only emitted on the success path — failures don't contribute
        to a latency SLO (they would skew p95 toward "fast" because
        a fail-fast classification path returns in milliseconds).
        """

    def inc_worker_active(self) -> None:
        """``worker_active_jobs`` Gauge inc — entry to a worker task."""

    def dec_worker_active(self) -> None:
        """``worker_active_jobs`` Gauge dec — exit (in ``finally``)."""

    def set_worker_concurrency(self, *, concurrency: int) -> None:
        """``worker_concurrency`` Gauge — set once at startup, used as
        the denominator in the B4 saturation query (§35 §4.3)."""

    # ----- API-side ----------------------------------------------------

    def inc_temp_link_serve(self, *, result: TempLinkServeResult) -> None:
        """``temp_link_serves_total{result}`` (A6).

        ``result=expired`` is excluded from the A6 denominator at
        query time — see ``ADR-0007`` §2.8 for the label vocabulary
        rationale.
        """


class NoopJobMetrics:
    """Used when ``METRICS_ENABLED=false`` and in tests that don't care.

    Intentionally **not** subclassing ``JobMetrics`` — Protocol
    structural typing makes that unnecessary, and inheritance would
    couple the Noop to method-signature drift in the Protocol.
    """

    def inc_bot_message_handled(
        self, *, outcome: HandlerOutcome, latency_bucket: LatencyBucket
    ) -> None:
        return

    def inc_job_created(self, *, platform: Platform) -> None:
        return

    def set_queue_depth(self, *, depth: int) -> None:
        return

    def inc_status_change(self, *, to: JobStatus, reason_class: ReasonClass) -> None:
        return

    def observe_job_duration(self, *, file_size_class: FileSizeClass, seconds: float) -> None:
        return

    def inc_worker_active(self) -> None:
        return

    def dec_worker_active(self) -> None:
        return

    def set_worker_concurrency(self, *, concurrency: int) -> None:
        return

    def inc_temp_link_serve(self, *, result: TempLinkServeResult) -> None:
        return
