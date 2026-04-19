"""Prometheus implementation of ``JobMetrics`` (``ADR-0007``).

All metric names mirror ``docs/35-metrics-and-slo.md`` §7 verbatim.
If you rename anything here, update §7, §3.1 (SLI definitions) and
the dashboard skeleton in §8 in the same change (P9). Renaming a
single metric without doing all three breaks the on-call runbook.

This class is *additive* — it shares its ``CollectorRegistry`` with
:class:`~app.infrastructure.metrics.prometheus_metrics.PrometheusRateLimitMetrics`
so a single ``/metrics`` endpoint serves both. See
:func:`app.composition._build_metrics`.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from app.domain.enums import JobStatus, Platform
from app.domain.observability import (
    FileSizeClass,
    HandlerOutcome,
    LatencyBucket,
    TempLinkServeResult,
)
from app.domain.reason_class import ReasonClass

# Buckets for ``job_duration_seconds``. Range covers the typical
# fast-IG-photo (~1 s) through the worst-case-1GB-video (~30 min).
# Cluster around the SLO band (90 s for ≤200 MB, 240 s for >200 MB —
# docs/35- §3.1 A4/A5) so the histogram_quantile actually has signal
# at the percentile we alert on.
_JOB_DURATION_BUCKETS = (
    0.5,
    1,
    2,
    5,
    10,
    30,
    60,
    120,
    240,
    600,
    1200,
    1800,
)


class PrometheusJobMetrics:
    """Concrete sink. One per process.

    Three processes (bot / worker / api) each get their own instance
    pointing at their own ``CollectorRegistry``; cross-process
    aggregation is the operator's Prometheus problem (``ADR-0007`` §2.5).
    """

    def __init__(self, *, registry: CollectorRegistry) -> None:
        # ----- Bot-side ------------------------------------------------
        self._bot_handled = Counter(
            "bot_message_handled_total",
            "Bot updates that completed processing, partitioned by "
            "outcome and a coarse latency bucket. Drives SLO A1.",
            labelnames=("outcome", "latency_bucket"),
            registry=registry,
        )
        self._jobs_created = Counter(
            "jobs_created_total",
            "Download jobs persisted by the bot's enqueue use case. Numerator base for SLO A2.",
            labelnames=("platform",),
            registry=registry,
        )
        self._queue_depth = Gauge(
            "arq_queue_depth",
            "Pending arq jobs waiting in the worker queue (ZCARD on "
            "the arq queue zset). Sampled every "
            "METRICS_QUEUE_SAMPLE_INTERVAL_S seconds. Drives SLO B3.",
            registry=registry,
        )

        # ----- Worker-side --------------------------------------------
        self._status_changed = Counter(
            "jobs_status_changed_total",
            "Job state-machine transitions, partitioned by destination "
            "state and failure classification. Drives SLO A2/A3 and "
            "the failure-by-reason_class panel.",
            labelnames=("to", "reason_class"),
            registry=registry,
        )
        self._job_duration = Histogram(
            "job_duration_seconds",
            "End-to-end processing time for completed jobs, by file "
            "size class. Drives SLO A4 (≤200 MB) and A5 (>200 MB).",
            labelnames=("file_size_class",),
            buckets=_JOB_DURATION_BUCKETS,
            registry=registry,
        )
        self._worker_active = Gauge(
            "worker_active_jobs",
            "Currently in-flight jobs on this worker. Maintained by a "
            "task wrapper around process_download_job (ADR-0007 §2.7).",
            registry=registry,
        )
        self._worker_concurrency = Gauge(
            "worker_concurrency",
            "Configured WORKER_CONCURRENCY for this process. Set once "
            "at startup so PromQL can compute B4 saturation.",
            registry=registry,
        )

        # ----- API-side -----------------------------------------------
        self._temp_link_serves = Counter(
            "temp_link_serves_total",
            "Outcomes of GET /d/{token}. The 'expired' label is "
            "excluded from the A6 denominator at query time — see "
            "ADR-0007 §2.8.",
            labelnames=("result",),
            registry=registry,
        )

    # ---------------------------------------------------------------
    # JobMetrics
    # ---------------------------------------------------------------

    def inc_bot_message_handled(
        self, *, outcome: HandlerOutcome, latency_bucket: LatencyBucket
    ) -> None:
        self._bot_handled.labels(outcome=outcome.value, latency_bucket=latency_bucket.value).inc()

    def inc_job_created(self, *, platform: Platform) -> None:
        self._jobs_created.labels(platform=platform.value).inc()

    def set_queue_depth(self, *, depth: int) -> None:
        self._queue_depth.set(depth)

    def inc_status_change(self, *, to: JobStatus, reason_class: ReasonClass) -> None:
        self._status_changed.labels(to=to.value, reason_class=reason_class.value).inc()

    def observe_job_duration(self, *, file_size_class: FileSizeClass, seconds: float) -> None:
        self._job_duration.labels(file_size_class=file_size_class.value).observe(seconds)

    def inc_worker_active(self) -> None:
        self._worker_active.inc()

    def dec_worker_active(self) -> None:
        self._worker_active.dec()

    def set_worker_concurrency(self, *, concurrency: int) -> None:
        self._worker_concurrency.set(concurrency)

    def inc_temp_link_serve(self, *, result: TempLinkServeResult) -> None:
        self._temp_link_serves.labels(result=result.value).inc()
