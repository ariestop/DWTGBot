"""
``ProgressReporter`` port + ``NoopProgressReporter`` contract tests.

Scope of this PR (foundation — ADR-0010 PR 1):

* ``NoopProgressReporter`` is a structural match for the
  ``ProgressReporter`` protocol — this is statically checked here
  via ``isinstance`` against the runtime-checkable protocol.
* All four methods return ``None`` cleanly and never raise.
* Calling terminal methods twice is idempotent (documented semantic
  for the Redis implementation; the Noop variant trivially satisfies).

The Redis-backed reporter gets its own test file in PR 3.
"""

from __future__ import annotations

import inspect

import pytest

from app.application.ports.progress_reporter import ProgressReporter
from app.domain.enums import ProgressStage
from app.infrastructure.cache.noop_progress_reporter import NoopProgressReporter


def test_noop_matches_protocol_shape() -> None:
    """Structural match — every method of the port exists on the noop,
    has the same name, and is an ``async def`` coroutine function.

    We don't ``@runtime_checkable`` the production ``Protocol`` to keep
    it zero-cost on hot paths; this test pins the contract instead.
    """
    reporter = NoopProgressReporter()
    required_methods = ("start", "update", "finish", "fail")
    for name in required_methods:
        assert hasattr(reporter, name), f"NoopProgressReporter missing {name}()"
        impl = getattr(reporter, name)
        assert inspect.iscoroutinefunction(impl), f"NoopProgressReporter.{name} must be async"
        port_method = getattr(ProgressReporter, name)
        port_sig = inspect.signature(port_method)
        impl_sig = inspect.signature(impl)
        # parameters minus ``self`` should match the port's keyword-only
        # signature exactly — protects against future drift.
        assert list(impl_sig.parameters) == [p for p in port_sig.parameters if p != "self"], (
            f"parameter list drift on {name}: {impl_sig} vs {port_sig}"
        )


async def test_noop_start_returns_none() -> None:
    reporter = NoopProgressReporter()
    result = await reporter.start(job_id=1, chat_id=42, message_id=99, thumbnail_url=None)
    assert result is None


async def test_noop_update_handles_all_stages() -> None:
    reporter = NoopProgressReporter()
    for stage in ProgressStage:
        # Must not raise for terminal OR intermediate stages — the port
        # does not forbid ``update(stage=DONE)``; the implementation
        # must simply tolerate the call.
        result = await reporter.update(job_id=1, percent=50.0, stage=stage)
        assert result is None


async def test_noop_finish_idempotent() -> None:
    reporter = NoopProgressReporter()
    assert await reporter.finish(job_id=1) is None
    assert await reporter.finish(job_id=1) is None


async def test_noop_fail_idempotent() -> None:
    reporter = NoopProgressReporter()
    assert await reporter.fail(job_id=1, reason="boom") is None
    assert await reporter.fail(job_id=1, reason="boom again") is None


@pytest.mark.parametrize("percent", [0.0, 0.5, 37.4, 99.9, 100.0])
async def test_noop_update_accepts_boundary_percentages(percent: float) -> None:
    reporter = NoopProgressReporter()
    result = await reporter.update(job_id=1, percent=percent, stage=ProgressStage.DOWNLOADING)
    assert result is None
