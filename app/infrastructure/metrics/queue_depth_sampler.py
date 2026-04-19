"""Periodic ``arq_queue_depth`` sampler (``ADR-0007`` §2.6).

Why a sampler instead of a scrape-time collector
------------------------------------------------
``prometheus_client`` exposes metrics through a synchronous
``generate_latest`` call — there is no async hook. Reading the queue
depth is async (Redis ``ZCARD``), so a custom collector would have to
bridge sync→async with ``asyncio.run_coroutine_threadsafe``, which
adds a deadlock surface during shutdown.

A bounded asyncio task is simpler, has zero deadlock surface, and is
"close enough" for the SLO B3 5-minute window: a 15-second sample
interval gives 20 samples per window, more than enough for
``max_over_time``.

The sampler also degrades gracefully — Redis hiccups are logged and
the gauge is left at its last value rather than reset to zero, which
would falsely depress alerts.
"""

from __future__ import annotations

import asyncio

from arq.connections import ArqRedis
from redis.exceptions import RedisError

from app.application.services.job_metrics import JobMetrics
from app.logging_config import get_logger

_logger = get_logger(__name__)


class QueueDepthSampler:
    """Owns one asyncio task. ``start`` is non-blocking; ``stop`` joins."""

    def __init__(
        self,
        *,
        pool: ArqRedis,
        queue_name: str,
        metrics: JobMetrics,
        interval_seconds: float,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be > 0")
        self._pool = pool
        self._queue_name = queue_name
        self._metrics = metrics
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="queue-depth-sampler")
        _logger.info(
            "queue_depth_sampler_started",
            queue=self._queue_name,
            interval_s=self._interval,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except TimeoutError:
            _logger.warning("queue_depth_sampler_stop_timeout")
            self._task.cancel()
        finally:
            self._task = None
            _logger.info("queue_depth_sampler_stopped")

    async def _loop(self) -> None:
        # Take an initial sample immediately so the dashboard shows a
        # value within seconds of bot startup, not after the first
        # full interval — important during incidents.
        await self._sample_once()
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
                # ``wait`` returned because the event was set → exit.
                return
            except TimeoutError:
                # Normal path — wake up, sample, sleep again.
                await self._sample_once()

    async def _sample_once(self) -> None:
        try:
            depth = await self._pool.zcard(self._queue_name)
        except RedisError as exc:
            # Don't reset the gauge — preserves the last known good
            # value so a transient Redis blip doesn't trigger
            # ``QueueBacklog cleared`` panels falsely.
            _logger.warning(
                "queue_depth_sample_failed",
                queue=self._queue_name,
                error_class=type(exc).__name__,
            )
            return
        try:
            self._metrics.set_queue_depth(depth=int(depth))
        except Exception:  # pragma: no cover - defensive
            _logger.exception("queue_depth_sample_observe_failed")
            return
        # DEBUG so it doesn't pollute INFO-level operational logs once
        # every 15 s, but is still available for hands-on tuning.
        _logger.debug(
            "queue_depth_sample",
            queue=self._queue_name,
            depth=int(depth),
        )
