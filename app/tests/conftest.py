"""
Shared test fixtures.

Unit tests deliberately avoid touching real DB/Redis/Telegram. Heavier
integration tests should be guarded by the ``integration`` marker.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide the bare minimum env so ``Settings()`` can construct."""
    monkeypatch.setenv("BOT_TOKEN", "0000000000:test-token")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("LOG_JSON", "false")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("STORAGE_PATH", os.environ.get("STORAGE_PATH", "/tmp/dwtgbot-tests/storage"))
    monkeypatch.setenv(
        "STORAGE_TMP_PATH", os.environ.get("STORAGE_TMP_PATH", "/tmp/dwtgbot-tests/tmp")
    )


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Settings is lru_cached; reset so per-test env changes take effect."""
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
