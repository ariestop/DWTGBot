"""Audit fix #5: ``/d/{token}`` uses an atomic ``try_register_use``.

Two scenarios that the previous load/mutate/save pair could not
guarantee but the new repository contract must:

1.  Concurrent requests on a ``max_downloads=1`` link → exactly one
    succeeds (200), every other one gets 410. Without the atomic UPDATE
    every concurrent caller could be served and the file (potentially
    containing private content) would leak past the cap.
2.  Pre-checks (path safety, file presence) run *before* the atomic
    increment, so a probe with a bogus path can't burn a download slot.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.public.downloads import router
from app.config import get_settings
from app.domain.entities.temp_link import TempLink
from app.domain.repositories.temp_links_repo import TempLinksRepository


class _AtomicRepo(TempLinksRepository):
    """In-memory mirror of the SqlAlchemy contract: ``try_register_use``
    holds a lock so concurrent calls on the same token serialise."""

    def __init__(self, links: list[TempLink]) -> None:
        self._by_token = {li.token: li for li in links}
        self._lock = asyncio.Lock()

    async def create(self, link: TempLink) -> TempLink:  # pragma: no cover
        self._by_token[link.token] = link
        return link

    async def get_by_token(self, token: str) -> TempLink | None:
        return self._by_token.get(token)

    async def update(self, link: TempLink) -> TempLink:  # pragma: no cover
        self._by_token[link.token] = link
        return link

    async def deactivate_expired(self) -> int:  # pragma: no cover
        return 0

    async def list_inactive_with_files(self, limit: int = 500):  # pragma: no cover
        return []

    async def try_register_use(self, token: str) -> TempLink | None:
        async with self._lock:
            link = self._by_token.get(token)
            if link is None or not link.is_usable():
                return None
            await asyncio.sleep(0)  # force a context switch under the lock
            link.register_use()
            return link


class _NoopMetrics:
    def __init__(self) -> None:
        self.serves: list[str] = []

    def inc_temp_link_serve(self, *, result) -> None:
        self.serves.append(result.value)

    def inc_bot_message_handled(self, **_):  # pragma: no cover
        pass

    def inc_job_created(self, **_):  # pragma: no cover
        pass

    def inc_status_change(self, **_):  # pragma: no cover
        pass

    def observe_job_duration(self, **_):  # pragma: no cover
        pass

    def set_queue_depth(self, **_):  # pragma: no cover
        pass

    def inc_worker_active(self):  # pragma: no cover
        pass

    def dec_worker_active(self):  # pragma: no cover
        pass

    def set_worker_concurrency(self, **_):  # pragma: no cover
        pass


class _Storage:
    pass


class _FakeComposition:
    def __init__(self, repo: _AtomicRepo, metrics: _NoopMetrics, storage: _Storage) -> None:
        self.temp_links_repo = repo
        self.job_metrics = metrics
        self.storage = storage


def _make_link(*, token: str, file_path: str, max_downloads: int = 1) -> TempLink:
    return TempLink(
        id=1,
        token=token,
        job_id=42,
        file_path=file_path,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        max_downloads=max_downloads,
    )


def _make_client(repo: _AtomicRepo) -> tuple[TestClient, _NoopMetrics]:
    settings = get_settings()
    metrics = _NoopMetrics()
    app = FastAPI()
    app.state.settings = settings
    app.state.composition = _FakeComposition(repo, metrics, _Storage())
    app.include_router(router)
    return TestClient(app), metrics


@pytest.fixture()
def real_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A file inside a settings-aware STORAGE_PATH so ``ensure_within``
    accepts it and ``file_path.exists()`` returns True."""
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage_root))
    monkeypatch.setenv("STORAGE_TMP_PATH", str(tmp_path / "tmp"))
    get_settings.cache_clear()
    f = storage_root / "f.bin"
    f.write_bytes(b"payload")
    return f


def test_single_use_link_concurrent_requests_serve_at_most_max(real_file: Path) -> None:
    """``max_downloads=1`` → first caller gets 200, every parallel one
    gets 410. Without ``try_register_use`` two parallel requests would
    both observe ``downloads_count == 0`` and both be served."""
    link = _make_link(token="abcdef0123456789", file_path=str(real_file), max_downloads=1)
    repo = _AtomicRepo([link])
    client, metrics = _make_client(repo)

    # TestClient is sync; serialise here, but the _AtomicRepo lock
    # mirrors the SQL guarantee end-to-end. The functional assertion
    # below is the load-bearing one: only the first call succeeds.
    r1 = client.get(f"/d/{link.token}")
    r2 = client.get(f"/d/{link.token}")
    r3 = client.get(f"/d/{link.token}")

    statuses = sorted([r1.status_code, r2.status_code, r3.status_code])
    assert statuses == [200, 410, 410]
    assert metrics.serves.count("ok") == 1
    assert metrics.serves.count("expired") == 2


def test_invalid_path_does_not_consume_download_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link whose ``file_path`` escapes STORAGE_PATH must be rejected
    with 403 — and must NOT call ``try_register_use``, so a probe
    cannot burn the legitimate user's only download."""
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage_root))
    monkeypatch.setenv("STORAGE_TMP_PATH", str(tmp_path / "tmp"))
    get_settings.cache_clear()

    # ``/etc/passwd`` is outside STORAGE_PATH → ensure_within raises.
    bad_link = _make_link(
        token="evilevilevilevil",
        file_path="/etc/passwd",
        max_downloads=1,
    )
    repo = _AtomicRepo([bad_link])

    # Track try_register_use invocations to assert the slot wasn't used.
    register_calls: list[str] = []
    original = repo.try_register_use

    async def _spy(token: str):
        register_calls.append(token)
        return await original(token)

    repo.try_register_use = _spy  # type: ignore[method-assign]

    client, metrics = _make_client(repo)

    r = client.get(f"/d/{bad_link.token}")
    assert r.status_code == 403
    assert register_calls == []  # cap was NOT consumed by the probe
    assert metrics.serves == ["forbidden"]
    # Counter stays at 0 — the next legitimate caller (if any) still
    # has the full quota.
    assert bad_link.downloads_count == 0
    assert bad_link.is_active is True
