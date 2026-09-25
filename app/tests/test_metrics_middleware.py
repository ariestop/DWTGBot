"""``instrument`` handler wrapper — ADR-0007 §2.1.

Checks outcome classification, the ``_handler_outcome`` hint, that
exceptions are re-raised for PTB's ``on_error``, and that exactly one
``bot_message_handled`` sample is recorded per call.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.bot.middleware import metrics_mw
from app.bot.middleware.metrics_mw import instrument
from app.domain.observability import HandlerOutcome, LatencyBucket
from app.exceptions import MediaNotFoundError, StorageError


class _FakeMetrics:
    def __init__(self) -> None:
        self.samples: list[tuple[HandlerOutcome, LatencyBucket]] = []

    def inc_bot_message_handled(
        self, *, outcome: HandlerOutcome, latency_bucket: LatencyBucket
    ) -> None:
        self.samples.append((outcome, latency_bucket))


@pytest.fixture
def metrics(monkeypatch: pytest.MonkeyPatch) -> _FakeMetrics:
    sink = _FakeMetrics()
    monkeypatch.setattr(
        metrics_mw,
        "get_container",
        lambda _bot_data: SimpleNamespace(job_metrics=sink),
    )
    return sink


def _context(user_data: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(bot_data={}, user_data=user_data)


async def test_successful_handler_records_ok_and_returns_result(metrics: _FakeMetrics) -> None:
    async def handler(_update: Any, _context: Any) -> str:
        return "done"

    result = await instrument(handler)(object(), _context())

    assert result == "done"
    assert [outcome for outcome, _ in metrics.samples] == [HandlerOutcome.OK]
    assert isinstance(metrics.samples[0][1], LatencyBucket)


async def test_handler_hint_downgrades_outcome_to_user_error(metrics: _FakeMetrics) -> None:
    async def handler(_update: Any, context: Any) -> None:
        context.user_data["_handler_outcome"] = HandlerOutcome.USER_ERROR

    await instrument(handler)(object(), _context(user_data={}))

    assert [outcome for outcome, _ in metrics.samples] == [HandlerOutcome.USER_ERROR]


async def test_non_enum_hint_is_ignored(metrics: _FakeMetrics) -> None:
    async def handler(_update: Any, context: Any) -> None:
        context.user_data["_handler_outcome"] = "user_error"

    await instrument(handler)(object(), _context(user_data={}))

    assert [outcome for outcome, _ in metrics.samples] == [HandlerOutcome.OK]


async def test_user_app_error_is_classified_as_user_error_and_reraised(
    metrics: _FakeMetrics,
) -> None:
    async def handler(_update: Any, _context: Any) -> None:
        raise MediaNotFoundError("gone")

    with pytest.raises(MediaNotFoundError):
        await instrument(handler)(object(), _context())

    assert [outcome for outcome, _ in metrics.samples] == [HandlerOutcome.USER_ERROR]


async def test_internal_app_error_is_classified_as_internal_error(metrics: _FakeMetrics) -> None:
    async def handler(_update: Any, _context: Any) -> None:
        raise StorageError("disk full")

    with pytest.raises(StorageError):
        await instrument(handler)(object(), _context())

    assert [outcome for outcome, _ in metrics.samples] == [HandlerOutcome.INTERNAL_ERROR]


async def test_unexpected_exception_is_internal_error_and_reraised(metrics: _FakeMetrics) -> None:
    async def handler(_update: Any, _context: Any) -> None:
        raise ValueError("bug")

    with pytest.raises(ValueError, match="bug"):
        await instrument(handler)(object(), _context())

    assert [outcome for outcome, _ in metrics.samples] == [HandlerOutcome.INTERNAL_ERROR]


def test_wrapper_keeps_handler_identity_for_logs() -> None:
    async def handle_start(_update: Any, _context: Any) -> None:
        """Start command."""

    wrapped = instrument(handle_start)

    assert wrapped.__name__ == "instrumented_handle_start"
    assert wrapped.__doc__ == "Start command."
