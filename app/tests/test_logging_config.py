"""Logging setup — PrintLogger + processor chain must not crash (API/bot)."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.logging_config import configure_logging, get_logger


def test_native_structlog_info_after_configure_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: structlog.stdlib.add_logger_name + PrintLogger raised AttributeError."""
    monkeypatch.setenv("LOG_JSON", "true")
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("dwtgbot.test")
    log.info("hello", smoke=True)


def test_stdlib_bridge_processors_get_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stdlib LogRecords must carry _record so logger name resolves without a bound logger."""
    import logging

    monkeypatch.setenv("LOG_JSON", "true")
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    settings = get_settings()
    configure_logging(settings)
    logging.getLogger("some.lib").warning("from stdlib")
