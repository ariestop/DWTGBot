"""Audit fix A5: /readyz does not leak exception detail into the body.

Before the fix, ``/readyz`` put ``str(exc)`` into the JSON response
whenever a check raised, which exposed asyncpg driver messages
containing socket paths — and in pathological cases credentials embedded
in DSNs. We verify the response body now carries only ``"fail"`` per
check (the stack trace goes to the structured log instead).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.internal.health import router
from app.config import Settings


class _BoomCore:
    async def ping_postgres(self) -> None:
        raise RuntimeError(
            "sensitive: user=admin password=hunter2 host=/var/run/postgresql/.s.PGSQL.5432"
        )

    async def ping_redis(self) -> bool:
        raise RuntimeError("ConnectionError: password=hunter2 leaked in driver msg")


class _BoomStorage:
    def init(self) -> None:
        raise RuntimeError("file:///etc/passwd permission denied")


@dataclass
class _Composition:
    core: Any
    storage: Any


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)  # type: ignore[call-arg]
    app.state.composition = _Composition(
        core=_BoomCore(),
        storage=_BoomStorage(),
    )
    app.include_router(router)
    return TestClient(app)


def test_readyz_503_body_has_no_exception_detail() -> None:
    client = _make_client()
    resp = client.get("/readyz")
    assert resp.status_code == 503

    body_text = resp.text
    assert "password" not in body_text
    assert "hunter2" not in body_text
    assert "admin" not in body_text
    assert "/etc/passwd" not in body_text
    assert "/var/run/postgresql" not in body_text

    payload = resp.json()
    # FastAPI wraps HTTPException.detail under "detail".
    detail = payload["detail"]
    assert detail == {"postgres": "fail", "redis": "fail", "storage": "fail"}


def test_readyz_ok_body_unchanged() -> None:
    """Happy path keeps the documented shape — no accidental regression."""

    class _OkCore:
        async def ping_postgres(self) -> None:
            return None

        async def ping_redis(self) -> bool:
            return True

    class _OkStorage:
        def init(self) -> None:
            return None

    app = FastAPI()
    app.state.settings = Settings(_env_file=None)  # type: ignore[call-arg]
    app.state.composition = _Composition(
        core=_OkCore(),
        storage=_OkStorage(),
    )
    app.include_router(router)
    resp = TestClient(app).get("/readyz")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ready"
    assert payload["checks"] == {"postgres": "ok", "redis": "ok", "storage": "ok"}
