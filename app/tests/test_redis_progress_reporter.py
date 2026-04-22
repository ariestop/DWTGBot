"""Tests for :class:`RedisProgressReporter` (ADR-0010 §2.2).

Uses a minimal fake async Redis that records the sequence of HSET /
EXPIRE / PUBLISH calls. We avoid a real Redis dep so the suite stays
fast and hermetic — the class contract is fully expressible through
the commands that actually hit the wire.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.config import get_settings
from app.domain.enums import ProgressStage
from app.infrastructure.cache.redis_progress_reporter import (
    RedisProgressReporter,
    _clamp_percent,
)


class _FakePipeline:
    def __init__(self, parent: _FakeAsyncRedis) -> None:
        self._parent = parent
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def hset(self, *args: Any, **kwargs: Any) -> _FakePipeline:
        self.calls.append(("hset", args, kwargs))
        return self

    def expire(self, *args: Any, **kwargs: Any) -> _FakePipeline:
        self.calls.append(("expire", args, kwargs))
        return self

    def publish(self, *args: Any, **kwargs: Any) -> _FakePipeline:
        self.calls.append(("publish", args, kwargs))
        return self

    async def execute(self) -> None:
        for name, args, kwargs in self.calls:
            self._parent.log.append((name, args, kwargs))

    async def __aenter__(self) -> _FakePipeline:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeAsyncRedis:
    def __init__(self) -> None:
        self.log: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        del transaction
        return _FakePipeline(self)


def _make_reporter() -> tuple[RedisProgressReporter, _FakeAsyncRedis]:
    fake = _FakeAsyncRedis()
    settings = get_settings()
    reporter = RedisProgressReporter(
        redis=fake,  # type: ignore[arg-type]
        redis_url="redis://localhost:6379/0",
        settings=settings,
    )
    return reporter, fake


def _collect(fake: _FakeAsyncRedis, op: str) -> list[tuple[Any, ...]]:
    return [args for name, args, _ in fake.log if name == op]


@pytest.mark.asyncio
async def test_start_writes_meta_and_progress_and_publishes() -> None:
    reporter, fake = _make_reporter()
    await reporter.start(job_id=42, chat_id=123, message_id=456, thumbnail_url="https://x/t.jpg")

    hset_calls = _collect(fake, "hset")
    # One HSET for meta, one for progress.
    assert len(hset_calls) == 2
    assert hset_calls[0][0] == "progress_meta:42"
    assert hset_calls[1][0] == "progress:42"

    expire_calls = _collect(fake, "expire")
    # Both keys get TTLs.
    assert {c[0] for c in expire_calls} == {"progress_meta:42", "progress:42"}

    publish_calls = _collect(fake, "publish")
    assert publish_calls == [("progress:events", "42")]


@pytest.mark.asyncio
async def test_update_writes_and_publishes() -> None:
    reporter, fake = _make_reporter()
    await reporter.update(job_id=7, percent=25.0, stage=ProgressStage.DOWNLOADING)

    hset = _collect(fake, "hset")
    assert hset[0][0] == "progress:7"
    # pipeline.hset(key, mapping={...}) — pull from kwargs.
    call_kwargs = fake.log[0][2]
    assert call_kwargs.get("mapping", {}).get("stage") == ProgressStage.DOWNLOADING.value

    assert _collect(fake, "publish") == [("progress:events", "7")]


@pytest.mark.asyncio
async def test_update_clamps_negative_and_overflow() -> None:
    reporter, fake = _make_reporter()
    await reporter.update(job_id=1, percent=-50.0, stage=ProgressStage.DOWNLOADING)
    # reset between calls to isolate
    reporter._state.pop(1, None)
    fake.log.clear()
    await reporter.update(job_id=1, percent=250.0, stage=ProgressStage.DOWNLOADING)

    mapping = fake.log[0][2].get("mapping", {})
    assert float(mapping["percent"]) == 100.0


@pytest.mark.asyncio
async def test_update_debounces_within_window_same_stage() -> None:
    reporter, fake = _make_reporter()
    # First call: always writes.
    await reporter.update(job_id=9, percent=10.0, stage=ProgressStage.DOWNLOADING)
    assert len(fake.log) > 0
    fake.log.clear()
    # Second call within PROGRESS_REDRAW_INTERVAL_SEC, tiny delta, same stage:
    # must be suppressed (no new writes, no publish).
    await reporter.update(job_id=9, percent=10.5, stage=ProgressStage.DOWNLOADING)
    assert fake.log == []


@pytest.mark.asyncio
async def test_update_does_not_debounce_on_stage_change() -> None:
    reporter, fake = _make_reporter()
    await reporter.update(job_id=9, percent=50.0, stage=ProgressStage.DOWNLOADING)
    fake.log.clear()
    # Same percent, different stage → must fire.
    await reporter.update(job_id=9, percent=50.0, stage=ProgressStage.PROCESSING)
    assert fake.log, "stage change must bypass debounce"


@pytest.mark.asyncio
async def test_update_does_not_debounce_on_big_delta() -> None:
    reporter, fake = _make_reporter()
    await reporter.update(job_id=9, percent=10.0, stage=ProgressStage.DOWNLOADING)
    fake.log.clear()
    # Big delta (> PROGRESS_DEBOUNCE_PERCENT default 3) must fire.
    await reporter.update(job_id=9, percent=80.0, stage=ProgressStage.DOWNLOADING)
    assert fake.log, "large delta must bypass debounce"


@pytest.mark.asyncio
async def test_finish_writes_terminal_and_clears_state() -> None:
    reporter, fake = _make_reporter()
    await reporter.update(job_id=9, percent=50.0, stage=ProgressStage.DOWNLOADING)
    assert 9 in reporter._state
    fake.log.clear()
    await reporter.finish(job_id=9)
    mapping = fake.log[0][2].get("mapping", {})
    assert mapping["stage"] == ProgressStage.DONE.value
    assert float(mapping["percent"]) == 100.0
    assert 9 not in reporter._state


@pytest.mark.asyncio
async def test_fail_attaches_reason_truncated() -> None:
    reporter, fake = _make_reporter()
    huge = "x" * 2000
    await reporter.fail(job_id=4, reason=huge)
    mapping = fake.log[0][2].get("mapping", {})
    assert mapping["stage"] == ProgressStage.FAILED.value
    assert len(mapping["reason"]) == 512


@pytest.mark.asyncio
async def test_terminal_stage_bypasses_debounce() -> None:
    reporter, fake = _make_reporter()
    await reporter.update(job_id=9, percent=95.0, stage=ProgressStage.UPLOADING)
    fake.log.clear()
    await reporter.finish(job_id=9)
    assert fake.log, "terminal stage must always write"


@pytest.mark.asyncio
async def test_redis_error_swallowed_by_update() -> None:
    from redis.exceptions import RedisError

    class _BoomPipeline(_FakePipeline):
        async def execute(self) -> None:
            raise RedisError("boom")

    class _BoomRedis(_FakeAsyncRedis):
        def pipeline(self, transaction: bool = True) -> _FakePipeline:
            del transaction
            return _BoomPipeline(self)

    settings = get_settings()
    reporter = RedisProgressReporter(
        redis=_BoomRedis(),  # type: ignore[arg-type]
        redis_url="redis://localhost:6379/0",
        settings=settings,
    )
    # Must not raise — progress failures are log-and-continue.
    await reporter.update(job_id=1, percent=10.0, stage=ProgressStage.DOWNLOADING)
    await reporter.start(job_id=1, chat_id=1, message_id=1, thumbnail_url=None)
    await reporter.finish(job_id=1)
    await reporter.fail(job_id=1, reason="x")


def test_clamp_percent_bounds() -> None:
    assert _clamp_percent(-1.0) == 0.0
    assert _clamp_percent(0.0) == 0.0
    assert _clamp_percent(50.5) == 50.5
    assert _clamp_percent(100.0) == 100.0
    assert _clamp_percent(100.0001) == 100.0


@pytest.mark.asyncio
async def test_terminal_after_long_gap_still_writes() -> None:
    reporter, fake = _make_reporter()
    await reporter.update(job_id=9, percent=50.0, stage=ProgressStage.DOWNLOADING)
    # Simulate no state at all (cleared) — terminal must still fire.
    reporter._state.pop(9, None)
    fake.log.clear()
    await reporter.fail(job_id=9, reason="timeout")
    await asyncio.sleep(0)  # let any awaits settle
    assert fake.log
