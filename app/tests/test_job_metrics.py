"""Tests for the job/queue/worker metrics pipeline (ADR-0007).

Covers:
- ``NoopJobMetrics`` is callable on every method (so production code
  never needs a None-check).
- ``PrometheusJobMetrics`` emits the right metric / label combo for
  each entry point.
- ``QueueDepthSampler`` updates the gauge, swallows ``RedisError``
  without resetting the gauge, and stops cleanly.
- ``EnqueueDownloadUseCase`` increments ``jobs_created_total`` only
  on the success path (cap-rejection MUST NOT count).
"""

from __future__ import annotations

import asyncio
import re

import pytest
from prometheus_client import CollectorRegistry
from prometheus_client.exposition import generate_latest
from redis.exceptions import RedisError

from app.application.dto.jobs import EnqueueDownloadInput
from app.application.services.job_metrics import NoopJobMetrics
from app.application.services.queue import QueueProducer
from app.application.use_cases.enqueue_download import EnqueueDownloadUseCase
from app.domain.entities.download_job import DownloadJob
from app.domain.enums import JobStatus, Platform
from app.domain.observability import (
    FileSizeClass,
    HandlerOutcome,
    LatencyBucket,
    TempLinkServeResult,
)
from app.domain.reason_class import ReasonClass
from app.domain.repositories.jobs_repo import JobsRepository
from app.exceptions import TooManyJobsError
from app.infrastructure.metrics.prometheus_job_metrics import PrometheusJobMetrics
from app.infrastructure.metrics.queue_depth_sampler import QueueDepthSampler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _registry_text(registry: CollectorRegistry) -> str:
    return generate_latest(registry).decode("utf-8")


def _counter_value(text: str, name: str, **labels: str) -> float:
    """Parse a single counter line out of the Prometheus text format.

    We deliberately avoid pulling in ``prometheus_client.parser`` —
    the regex is small and the test failure modes are easier to read.
    """
    # ``prometheus_client`` emits labels in the order given to the
    # constructor's ``labelnames``; for stability we sort here so the
    # test expresses *which* labels match, not *in what order* the
    # exposition format happens to render them.
    label_re = ",".join(rf'{k}="{re.escape(v)}"' for k, v in sorted(labels.items()))
    if label_re:
        pattern = rf"^{re.escape(name)}\{{{label_re}\}} (\S+)"
    else:
        pattern = rf"^{re.escape(name)} (\S+)"
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        return 0.0
    return float(match.group(1))


# ---------------------------------------------------------------------------
# Noop sink
# ---------------------------------------------------------------------------


def test_noop_job_metrics_is_callable_on_every_method() -> None:
    sink = NoopJobMetrics()
    sink.inc_bot_message_handled(outcome=HandlerOutcome.OK, latency_bucket=LatencyBucket.LT_500MS)
    sink.inc_job_created(platform=Platform.YOUTUBE)
    sink.set_queue_depth(depth=42)
    sink.inc_status_change(to=JobStatus.DONE, reason_class=ReasonClass.OK)
    sink.observe_job_duration(file_size_class=FileSizeClass.LT_200MB, seconds=12.5)
    sink.inc_worker_active()
    sink.dec_worker_active()
    sink.set_worker_concurrency(concurrency=4)
    sink.inc_temp_link_serve(result=TempLinkServeResult.OK)


# ---------------------------------------------------------------------------
# PrometheusJobMetrics
# ---------------------------------------------------------------------------


def test_prometheus_job_metrics_emits_bot_handled_with_labels() -> None:
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)

    sink.inc_bot_message_handled(outcome=HandlerOutcome.OK, latency_bucket=LatencyBucket.LT_1500MS)
    sink.inc_bot_message_handled(
        outcome=HandlerOutcome.USER_ERROR, latency_bucket=LatencyBucket.LT_500MS
    )

    text = _registry_text(registry)
    assert (
        _counter_value(text, "bot_message_handled_total", outcome="ok", latency_bucket="lt_1500ms")
        == 1.0
    )
    assert (
        _counter_value(
            text,
            "bot_message_handled_total",
            outcome="user_error",
            latency_bucket="lt_500ms",
        )
        == 1.0
    )


def test_prometheus_job_metrics_emits_jobs_created_per_platform() -> None:
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)

    sink.inc_job_created(platform=Platform.YOUTUBE)
    sink.inc_job_created(platform=Platform.YOUTUBE)
    sink.inc_job_created(platform=Platform.INSTAGRAM)

    text = _registry_text(registry)
    assert _counter_value(text, "jobs_created_total", platform="youtube") == 2.0
    assert _counter_value(text, "jobs_created_total", platform="instagram") == 1.0


def test_prometheus_job_metrics_status_change_uses_real_jobstatus_values() -> None:
    """ADR-0007 §2.2: ``to`` label uses the four ``JobStatus`` values
    (pending/processing/done/failed), NOT the aspirational §35 §4.1
    set. This test makes the deviation explicit."""
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)

    sink.inc_status_change(to=JobStatus.PROCESSING, reason_class=ReasonClass.OK)
    sink.inc_status_change(to=JobStatus.DONE, reason_class=ReasonClass.OK)
    sink.inc_status_change(to=JobStatus.FAILED, reason_class=ReasonClass.PROVIDER_ERROR)

    text = _registry_text(registry)
    assert (
        _counter_value(text, "jobs_status_changed_total", to="processing", reason_class="ok") == 1.0
    )
    assert _counter_value(text, "jobs_status_changed_total", to="done", reason_class="ok") == 1.0
    assert (
        _counter_value(
            text,
            "jobs_status_changed_total",
            to="failed",
            reason_class="provider_error",
        )
        == 1.0
    )


def test_prometheus_job_metrics_duration_histogram_buckets_visible() -> None:
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)

    sink.observe_job_duration(file_size_class=FileSizeClass.LT_200MB, seconds=45.0)
    sink.observe_job_duration(file_size_class=FileSizeClass.LT_200MB, seconds=300.0)

    text = _registry_text(registry)
    # The histogram emits one ``_count`` series per label set; that's
    # the simplest invariant to assert without coupling to bucket edges.
    assert 'job_duration_seconds_count{file_size_class="lt_200mb"} 2.0' in text
    # And one ``_sum`` we can sanity-check.
    sum_match = re.search(r'job_duration_seconds_sum\{file_size_class="lt_200mb"\} (\S+)', text)
    assert sum_match is not None
    assert float(sum_match.group(1)) == pytest.approx(345.0)


def test_prometheus_job_metrics_worker_gauges() -> None:
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)

    sink.set_worker_concurrency(concurrency=4)
    sink.inc_worker_active()
    sink.inc_worker_active()
    sink.dec_worker_active()

    text = _registry_text(registry)
    assert "worker_concurrency 4.0" in text
    assert "worker_active_jobs 1.0" in text


def test_prometheus_job_metrics_temp_link_serve_label_set() -> None:
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)

    for r in (
        TempLinkServeResult.OK,
        TempLinkServeResult.NOT_FOUND,
        TempLinkServeResult.EXPIRED,
    ):
        sink.inc_temp_link_serve(result=r)

    text = _registry_text(registry)
    assert _counter_value(text, "temp_link_serves_total", result="ok") == 1.0
    assert _counter_value(text, "temp_link_serves_total", result="not_found") == 1.0
    assert _counter_value(text, "temp_link_serves_total", result="expired") == 1.0


def test_prometheus_job_metrics_queue_depth_gauge_is_idempotent() -> None:
    """Setting the same value twice is a no-op for Prometheus — guards
    against a regression where ``set`` is replaced by ``inc``."""
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)

    sink.set_queue_depth(depth=7)
    sink.set_queue_depth(depth=7)
    sink.set_queue_depth(depth=12)

    text = _registry_text(registry)
    assert "arq_queue_depth 12.0" in text


# ---------------------------------------------------------------------------
# QueueDepthSampler
# ---------------------------------------------------------------------------


class _StubArqPool:
    """Minimal stand-in for arq's pool — only ``zcard`` is used by the sampler."""

    def __init__(self, depths: list[int | Exception]) -> None:
        self._depths = depths
        self._calls = 0

    async def zcard(self, _key: str) -> int:
        if self._calls >= len(self._depths):
            await asyncio.sleep(0)
            return self._depths[-1] if isinstance(self._depths[-1], int) else 0
        value = self._depths[self._calls]
        self._calls += 1
        if isinstance(value, Exception):
            raise value
        return value


async def test_queue_depth_sampler_publishes_initial_then_periodic_value() -> None:
    pool = _StubArqPool([3, 7])
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)

    sampler = QueueDepthSampler(
        pool=pool,  # type: ignore[arg-type]
        queue_name="arq:test",
        metrics=sink,
        interval_seconds=0.05,
    )
    await sampler.start()
    # Allow the initial sample + at least one tick.
    await asyncio.sleep(0.15)
    await sampler.stop()

    text = _registry_text(registry)
    # Last observed value wins; either 7 (sampler caught up) or 3
    # (still on the first tick) — we just want a *real* observation.
    assert "arq_queue_depth 7.0" in text or "arq_queue_depth 3.0" in text


async def test_queue_depth_sampler_swallows_redis_errors() -> None:
    """Transient Redis blips MUST NOT crash the sampler or reset the
    gauge to zero — that would falsely trigger ``QueueBacklog cleared``
    panels and mask real backlog drains."""
    pool = _StubArqPool([5, RedisError("boom"), 9])
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)

    sampler = QueueDepthSampler(
        pool=pool,  # type: ignore[arg-type]
        queue_name="arq:test",
        metrics=sink,
        interval_seconds=0.05,
    )
    await sampler.start()
    await asyncio.sleep(0.25)
    await sampler.stop()

    text = _registry_text(registry)
    # The gauge value must be a real observation (5 or 9), never the
    # 0.0 default that would imply "queue is empty".
    assert "arq_queue_depth 0.0" not in text


async def test_queue_depth_sampler_stop_is_idempotent() -> None:
    pool = _StubArqPool([1])
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)

    sampler = QueueDepthSampler(
        pool=pool,  # type: ignore[arg-type]
        queue_name="arq:test",
        metrics=sink,
        interval_seconds=0.05,
    )
    await sampler.start()
    await sampler.stop()
    await sampler.stop()  # second call must be a no-op


def test_queue_depth_sampler_rejects_zero_interval() -> None:
    pool = _StubArqPool([1])
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)
    with pytest.raises(ValueError):
        QueueDepthSampler(
            pool=pool,  # type: ignore[arg-type]
            queue_name="arq:test",
            metrics=sink,
            interval_seconds=0,
        )


# ---------------------------------------------------------------------------
# EnqueueDownloadUseCase integration
# ---------------------------------------------------------------------------


class _FakeJobsRepo(JobsRepository):
    def __init__(self, *, active_for_user: int = 0) -> None:
        self._active = active_for_user
        self.created: list[DownloadJob] = []

    async def count_active_for_user(self, user_id: int) -> int:
        return self._active

    async def create(self, job: DownloadJob) -> DownloadJob:
        job.id = 42
        self.created.append(job)
        return job

    async def get(self, job_id: int):  # pragma: no cover - unused here
        return None

    async def update(self, job: DownloadJob) -> DownloadJob:  # pragma: no cover
        return job

    async def increment_retries(self, job_id: int) -> int:  # pragma: no cover
        return 0

    async def create_if_under_cap(self, job: DownloadJob, *, cap: int) -> DownloadJob | None:
        # Mirror the production semantics for the metrics test fixture:
        # one coroutine call, returns None on cap.
        if self._active >= cap:
            return None
        return await self.create(job)

    async def reap_orphan_processing(self, *, older_than_seconds: int) -> int:
        # S1 (audit fix): exercised by cleanup_worker, not the enqueue path.
        del older_than_seconds
        return 0


class _FakeQueue(QueueProducer):
    def __init__(self) -> None:
        self.payloads: list[object] = []

    async def enqueue_download(self, payload) -> None:  # type: ignore[no-untyped-def]
        self.payloads.append(payload)


async def test_enqueue_use_case_increments_jobs_created_on_success() -> None:
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)
    use_case = EnqueueDownloadUseCase(
        jobs_repo=_FakeJobsRepo(),
        queue=_FakeQueue(),
        max_concurrent_per_user=3,
        metrics=sink,
    )

    await use_case.execute(
        EnqueueDownloadInput(
            user_id=1,
            chat_id=1,
            source_url="https://x.test/v",
            platform=Platform.YOUTUBE,
            selected_option_key="opt",
            correlation_id="c",
        )
    )

    text = _registry_text(registry)
    assert _counter_value(text, "jobs_created_total", platform="youtube") == 1.0


async def test_enqueue_use_case_does_not_increment_when_capped() -> None:
    """Cap rejection MUST NOT count as a created job — otherwise a
    user repeatedly tripping the cap would inflate A2's denominator."""
    registry = CollectorRegistry()
    sink = PrometheusJobMetrics(registry=registry)
    use_case = EnqueueDownloadUseCase(
        jobs_repo=_FakeJobsRepo(active_for_user=10),
        queue=_FakeQueue(),
        max_concurrent_per_user=3,
        metrics=sink,
    )

    with pytest.raises(TooManyJobsError):
        await use_case.execute(
            EnqueueDownloadInput(
                user_id=1,
                chat_id=1,
                source_url="https://x.test/v",
                platform=Platform.YOUTUBE,
                selected_option_key="opt",
                correlation_id="c",
            )
        )

    text = _registry_text(registry)
    # Either absent (preferred — no series at all) or zero.
    assert _counter_value(text, "jobs_created_total", platform="youtube") == 0.0
