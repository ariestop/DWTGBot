"""Internal API token middleware (A16)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.application.dto.jobs import EnqueueDownloadResult
from app.config import Settings


class _OkConnCtx:
    async def __aenter__(self) -> _OkConnCtx:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, _stmt: object) -> None:
        return None


class _OkEngine:
    def connect(self) -> _OkConnCtx:
        return _OkConnCtx()


class _OkRedis:
    async def ping(self) -> bool:
        return True


class _OkStorage:
    def init(self) -> None:
        return None


@dataclass
class _Core:
    engine: _OkEngine
    redis: _OkRedis


@dataclass
class _FakeEnqueue:
    async def execute(self, _payload: object) -> EnqueueDownloadResult:
        return EnqueueDownloadResult(job_id=42)


@dataclass
class _Composition:
    core: _Core
    storage: _OkStorage
    enqueue_download: _FakeEnqueue | None

    async def aclose(self) -> None:
        return None


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    api_internal_token: str,
    internal_test_token: str = "",
) -> TestClient:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    object.__setattr__(settings, "API_INTERNAL_TOKEN", api_internal_token)
    object.__setattr__(settings, "INTERNAL_TEST_TOKEN", internal_test_token)

    del monkeypatch
    app = create_app(settings)
    app.state.settings = settings
    app.state.composition = _Composition(
        core=_Core(engine=_OkEngine(), redis=_OkRedis()),
        storage=_OkStorage(),
        enqueue_download=_FakeEnqueue(),
    )
    return TestClient(app)


def test_healthz_stays_public_without_internal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, api_internal_token="secret")

    resp = client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_requires_internal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, api_internal_token="secret")

    resp = client.get("/readyz")

    assert resp.status_code == 401


def test_readyz_rejects_invalid_internal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, api_internal_token="secret")

    resp = client.get("/readyz", headers={"X-Internal-Token": "wrong"})

    assert resp.status_code == 401


def test_readyz_accepts_valid_internal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, api_internal_token="secret")

    resp = client.get("/readyz", headers={"X-Internal-Token": "secret"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_internal_route_requires_internal_token_before_test_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client(
        monkeypatch,
        api_internal_token="secret",
        internal_test_token="dev-token",
    )

    resp = client.post(
        "/internal/test/enqueue",
        json={
            "user_id": 1,
            "chat_id": 2,
            "source_url": "https://youtu.be/abc",
            "platform": "youtube",
            "selected_option_key": "video_720",
        },
        headers={"X-Internal-Test-Token": "dev-token"},
    )

    assert resp.status_code == 401


def test_internal_route_accepts_both_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(
        monkeypatch,
        api_internal_token="secret",
        internal_test_token="dev-token",
    )

    resp = client.post(
        "/internal/test/enqueue",
        json={
            "user_id": 1,
            "chat_id": 2,
            "source_url": "https://youtu.be/abc",
            "platform": "youtube",
            "selected_option_key": "video_720",
        },
        headers={
            "X-Internal-Token": "secret",
            "X-Internal-Test-Token": "dev-token",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["job_id"] == 42


def test_dev_mode_stays_open_when_api_internal_token_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client(
        monkeypatch,
        api_internal_token="",
        internal_test_token="dev-token",
    )

    resp = client.post(
        "/internal/test/enqueue",
        json={
            "user_id": 1,
            "chat_id": 2,
            "source_url": "https://youtu.be/abc",
            "platform": "youtube",
            "selected_option_key": "video_720",
        },
        headers={"X-Internal-Test-Token": "dev-token"},
    )

    assert resp.status_code == 200
