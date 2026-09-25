"""Cancel-button callback — ADR-0010 §2.1.

The handler must ack the query first, set the cooperative cancel flag,
and publish the terminal ``CANCELLED`` progress event even when the
flag write fails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from app.bot.callbacks import cancel_job as cancel_job_mod
from app.bot.callbacks.cancel_job import handle_cancel_job_callback
from app.bot.callbacks.codec import CancelJobCallback


@pytest.fixture(autouse=True)
def _patch_get_container(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cancel_job_mod,
        "get_container",
        lambda bot_data: bot_data["__test_container__"],
    )


@dataclass
class _FakeQuery:
    data: str | None
    answers: list[str | None] = field(default_factory=list)

    async def answer(self, text: str | None = None, **_: Any) -> None:
        self.answers.append(text)


class _FakeCancelStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.requested: list[int] = []
        self._fail = fail

    async def request(self, *, job_id: int) -> None:
        if self._fail:
            raise RuntimeError("redis unavailable")
        self.requested.append(job_id)


class _FakeReporter:
    def __init__(self) -> None:
        self.cancelled: list[int] = []

    async def cancel(self, *, job_id: int) -> None:
        self.cancelled.append(job_id)


def _update(query: _FakeQuery) -> SimpleNamespace:
    return SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=4242),
    )


def _context(container: object) -> SimpleNamespace:
    return SimpleNamespace(bot_data={"__test_container__": container})


async def test_cancel_sets_flag_and_publishes_cancelled_event() -> None:
    store, reporter = _FakeCancelStore(), _FakeReporter()
    query = _FakeQuery(data=CancelJobCallback(job_id=17).encode())
    container = SimpleNamespace(job_cancellation=store, progress_reporter=reporter)

    await handle_cancel_job_callback(_update(query), _context(container))

    assert query.answers == ["Отменяется…"]
    assert store.requested == [17]
    assert reporter.cancelled == [17]


async def test_foreign_callback_data_is_ignored_without_answer() -> None:
    store, reporter = _FakeCancelStore(), _FakeReporter()
    query = _FakeQuery(data="dl|abc|video_720")
    container = SimpleNamespace(job_cancellation=store, progress_reporter=reporter)

    await handle_cancel_job_callback(_update(query), _context(container))

    assert query.answers == []
    assert store.requested == []
    assert reporter.cancelled == []


async def test_query_without_data_is_ignored() -> None:
    query = _FakeQuery(data=None)

    await handle_cancel_job_callback(_update(query), _context(SimpleNamespace()))

    assert query.answers == []


async def test_missing_dependencies_still_answer_the_query() -> None:
    query = _FakeQuery(data=CancelJobCallback(job_id=3).encode())
    container = SimpleNamespace(job_cancellation=None, progress_reporter=None)

    await handle_cancel_job_callback(_update(query), _context(container))

    assert query.answers == ["Отменяется…"]


async def test_flag_write_failure_still_publishes_cancelled_event() -> None:
    store, reporter = _FakeCancelStore(fail=True), _FakeReporter()
    query = _FakeQuery(data=CancelJobCallback(job_id=9).encode())
    container = SimpleNamespace(job_cancellation=store, progress_reporter=reporter)

    await handle_cancel_job_callback(_update(query), _context(container))

    assert store.requested == []
    assert reporter.cancelled == [9]
