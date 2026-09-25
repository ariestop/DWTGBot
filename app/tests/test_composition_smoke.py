"""Smoke test: real composition builders wire and close without I/O.

SQLAlchemy engines and redis-py clients connect lazily, so building the
worker, api and cleanup compositions with production classes catches
wiring regressions (renamed kwargs, missing dependencies) without a DB,
Redis or Telegram. ``build_bot`` is excluded: it opens an arq pool.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application.ports.progress_reporter import NoopProgressReporter
from app.application.use_cases.process_download import ProcessDownloadUseCase
from app.composition import build_api, build_cleanup, build_worker
from app.config import get_settings
from app.infrastructure.cache.redis_progress_reporter import RedisProgressReporter
from app.infrastructure.storage.local_storage import LocalStorage
from app.infrastructure.telegram.sender import TelegramSender


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("STORAGE_TMP_PATH", str(tmp_path / "tmp"))
    monkeypatch.setenv("METRICS_ENABLED", "false")


@pytest.mark.parametrize(
    ("instant_download", "reporter_type"),
    [(True, RedisProgressReporter), (False, NoopProgressReporter)],
)
async def test_build_worker_wires_real_dependencies(
    monkeypatch: pytest.MonkeyPatch, instant_download: bool, reporter_type: type
) -> None:
    monkeypatch.setenv("INSTANT_DOWNLOAD_ENABLED", str(instant_download).lower())

    composition = build_worker(get_settings())
    try:
        assert isinstance(composition.use_case, ProcessDownloadUseCase)
        assert isinstance(composition.sender, TelegramSender)
        assert isinstance(composition.storage, LocalStorage)
        assert isinstance(composition.progress_reporter, reporter_type)
        assert composition.metrics_server is None
    finally:
        await composition.aclose()


async def test_build_api_skips_dev_helpers_without_internal_token() -> None:
    composition = await build_api(get_settings())
    try:
        assert isinstance(composition.storage, LocalStorage)
        assert composition.arq_pool is None
        assert composition.enqueue_download is None
    finally:
        await composition.aclose()


async def test_build_cleanup_initialises_storage_roots(tmp_path: Path) -> None:
    composition = await build_cleanup(get_settings())
    try:
        assert isinstance(composition.storage, LocalStorage)
        assert (tmp_path / "storage").is_dir()
    finally:
        await composition.aclose()
