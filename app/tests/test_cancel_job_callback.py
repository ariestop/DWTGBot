"""Cancel-button callback — ADR-0010 §2.1.

The handler must check that the job belongs to the caller, ack the
query, set the cooperative cancel flag, and publish the terminal
``CANCELLED`` progress event even when the flag write fails. A job that
is missing, foreign, or cannot be looked up is never cancelled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from app.bot.callbacks import cancel_job as cancel_job_mod
from app.bot.callbacks.cancel_job import (
    CANCEL_ACCEPTED_TEXT,
    CANCEL_DENIED_TEXT,
    handle_cancel_job_callback,
)
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


CALLER_ID = 42


class _FakeJobsRepo:
    """``get`` returns a job owned by ``owners[job_id]``, or ``None``."""

    def __init__(self, owners: dict[int, int] | None = None, *, fail: bool = False) -> None:
        self._owners = owners or {}
        self._fail = fail
        self.looked_up: list[int] = []

    async def get(self, job_id: int) -> SimpleNamespace | None:
        self.looked_up.append(job_id)
        if self._fail:
            raise RuntimeError("db unavailable")
        owner = self._owners.get(job_id)
        return None if owner is None else SimpleNamespace(id=job_id, user_id=owner)


def _container(store: object, reporter: object, jobs_repo: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        job_cancellation=store,
        progress_reporter=reporter,
        jobs_repo=jobs_repo,
    )


def _owned(*job_ids: int) -> _FakeJobsRepo:
    return _FakeJobsRepo(dict.fromkeys(job_ids, CALLER_ID))


def _update(query: _FakeQuery) -> SimpleNamespace:
    return SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=CALLER_ID),
        effective_chat=SimpleNamespace(id=4242),
    )


def _context(container: object) -> SimpleNamespace:
    return SimpleNamespace(bot_data={"__test_container__": container})


async def test_cancel_sets_flag_and_publishes_cancelled_event() -> None:
    store, reporter = _FakeCancelStore(), _FakeReporter()
    query = _FakeQuery(data=CancelJobCallback(job_id=17).encode())
    container = _container(store, reporter, _owned(17))

    await handle_cancel_job_callback(_update(query), _context(container))

    assert query.answers == [CANCEL_ACCEPTED_TEXT]
    assert store.requested == [17]
    assert reporter.cancelled == [17]


async def test_foreign_callback_data_is_ignored_without_answer() -> None:
    store, reporter = _FakeCancelStore(), _FakeReporter()
    query = _FakeQuery(data="dl|abc|video_720")
    container = _container(store, reporter, _owned(17))

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
    container = _container(None, None, None)

    await handle_cancel_job_callback(_update(query), _context(container))

    assert query.answers == [CANCEL_ACCEPTED_TEXT]


async def test_flag_write_failure_still_publishes_cancelled_event() -> None:
    store, reporter = _FakeCancelStore(fail=True), _FakeReporter()
    query = _FakeQuery(data=CancelJobCallback(job_id=9).encode())
    container = _container(store, reporter, _owned(9))

    await handle_cancel_job_callback(_update(query), _context(container))

    assert store.requested == []
    assert reporter.cancelled == [9]


async def test_foreign_job_is_not_cancelled() -> None:
    """A crafted ``cj|<id>`` for another user's job changes nothing."""
    store, reporter = _FakeCancelStore(), _FakeReporter()
    query = _FakeQuery(data=CancelJobCallback(job_id=5).encode())
    container = _container(store, reporter, _FakeJobsRepo({5: CALLER_ID + 1}))

    await handle_cancel_job_callback(_update(query), _context(container))

    assert query.answers == [CANCEL_DENIED_TEXT]
    assert store.requested == []
    assert reporter.cancelled == []


async def test_unknown_job_is_not_cancelled() -> None:
    store, reporter = _FakeCancelStore(), _FakeReporter()
    query = _FakeQuery(data=CancelJobCallback(job_id=404).encode())
    repo = _owned(1)
    container = _container(store, reporter, repo)

    await handle_cancel_job_callback(_update(query), _context(container))

    assert repo.looked_up == [404]
    assert query.answers == [CANCEL_DENIED_TEXT]
    assert store.requested == []
    assert reporter.cancelled == []


async def test_lookup_failure_fails_closed() -> None:
    store, reporter = _FakeCancelStore(), _FakeReporter()
    query = _FakeQuery(data=CancelJobCallback(job_id=7).encode())
    container = _container(store, reporter, _FakeJobsRepo(fail=True))

    await handle_cancel_job_callback(_update(query), _context(container))

    assert query.answers == [CANCEL_DENIED_TEXT]
    assert store.requested == []
    assert reporter.cancelled == []


async def test_missing_jobs_repo_cancels_nothing() -> None:
    """Without the repo ownership is unknowable — never cancel blind."""
    store, reporter = _FakeCancelStore(), _FakeReporter()
    query = _FakeQuery(data=CancelJobCallback(job_id=3).encode())
    container = _container(store, reporter, None)

    await handle_cancel_job_callback(_update(query), _context(container))

    assert query.answers == [CANCEL_ACCEPTED_TEXT]
    assert store.requested == []
    assert reporter.cancelled == []
