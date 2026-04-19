"""``POST /internal/test/enqueue`` — gating + happy path.

This endpoint is purely for capacity tests (`docs/37-load-and-capacity.md`).
It is hard-disabled in production via Settings.validate_runtime; here we
verify the runtime gate (404 when token unset, 401 when wrong token, 200 +
job_id when correct) without spinning up arq/Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.internal.test_enqueue import router
from app.application.dto.jobs import EnqueueDownloadResult
from app.config import Settings, get_settings
from app.exceptions import TooManyJobsError


@dataclass
class _FakeEnqueue:
    next_id: int = 42
    raise_too_many: bool = False
    calls: list[object] | None = None

    def __post_init__(self) -> None:
        self.calls = []

    async def execute(self, payload):  # type: ignore[no-untyped-def]
        assert self.calls is not None
        self.calls.append(payload)
        if self.raise_too_many:
            raise TooManyJobsError("cap hit")
        return EnqueueDownloadResult(job_id=self.next_id)


@dataclass
class _FakeComposition:
    enqueue_download: _FakeEnqueue | None


def _make_client(settings: Settings, enqueue: _FakeEnqueue | None) -> TestClient:
    app = FastAPI()
    app.state.settings = settings
    app.state.composition = _FakeComposition(enqueue_download=enqueue)
    app.include_router(router)
    return TestClient(app)


_PAYLOAD = {
    "user_id": 11,
    "chat_id": 22,
    "source_url": "https://youtu.be/abc",
    "platform": "youtube",
    "selected_option_key": "video_720",
}


def test_returns_404_when_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INTERNAL_TEST_TOKEN", raising=False)
    get_settings.cache_clear()
    client = _make_client(get_settings(), enqueue=None)
    r = client.post("/internal/test/enqueue", json=_PAYLOAD)
    assert r.status_code == 404


def test_returns_401_when_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_TEST_TOKEN", "right-token")
    get_settings.cache_clear()
    enqueue = _FakeEnqueue()
    client = _make_client(get_settings(), enqueue=enqueue)
    r = client.post(
        "/internal/test/enqueue",
        json=_PAYLOAD,
        headers={"X-Internal-Test-Token": "wrong-token"},
    )
    assert r.status_code == 401
    assert enqueue.calls == []


def test_returns_401_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_TEST_TOKEN", "right-token")
    get_settings.cache_clear()
    enqueue = _FakeEnqueue()
    client = _make_client(get_settings(), enqueue=enqueue)
    r = client.post("/internal/test/enqueue", json=_PAYLOAD)
    assert r.status_code == 401
    assert enqueue.calls == []


def test_happy_path_returns_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_TEST_TOKEN", "right-token")
    get_settings.cache_clear()
    enqueue = _FakeEnqueue(next_id=99)
    client = _make_client(get_settings(), enqueue=enqueue)
    r = client.post(
        "/internal/test/enqueue",
        json=_PAYLOAD,
        headers={"X-Internal-Test-Token": "right-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == 99
    assert isinstance(body["correlation_id"], str) and body["correlation_id"]
    assert enqueue.calls and enqueue.calls[0].user_id == 11  # type: ignore[union-attr]


def test_propagates_per_user_cap_as_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_TEST_TOKEN", "right-token")
    get_settings.cache_clear()
    enqueue = _FakeEnqueue(raise_too_many=True)
    client = _make_client(get_settings(), enqueue=enqueue)
    r = client.post(
        "/internal/test/enqueue",
        json=_PAYLOAD,
        headers={"X-Internal-Test-Token": "right-token"},
    )
    assert r.status_code == 429


def test_validates_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_TEST_TOKEN", "right-token")
    get_settings.cache_clear()
    enqueue = _FakeEnqueue()
    client = _make_client(get_settings(), enqueue=enqueue)
    bad = dict(_PAYLOAD)
    bad["platform"] = "myspace"  # not a known platform
    r = client.post(
        "/internal/test/enqueue",
        json=bad,
        headers={"X-Internal-Test-Token": "right-token"},
    )
    assert r.status_code == 422
