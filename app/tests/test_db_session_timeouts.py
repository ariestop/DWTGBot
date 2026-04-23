"""A24: SQLAlchemy async engine carries asyncpg timeout settings."""

from __future__ import annotations

from unittest.mock import Mock

from app.config import Settings
from app.infrastructure.db.session import build_engine


def test_build_engine_passes_asyncpg_timeout_connect_args(monkeypatch) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    object.__setattr__(settings, "DB_STATEMENT_TIMEOUT_S", 31)
    object.__setattr__(settings, "DB_CONNECT_TIMEOUT_S", 11)
    object.__setattr__(settings, "DB_IDLE_IN_TX_TIMEOUT_MS", 65000)

    captured: dict[str, object] = {}

    def _fake_create_async_engine(url: str, **kwargs: object) -> Mock:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return Mock()

    monkeypatch.setattr(
        "app.infrastructure.db.session.create_async_engine",
        _fake_create_async_engine,
    )

    build_engine(settings)

    kwargs = captured["kwargs"]
    assert kwargs["connect_args"] == {
        "command_timeout": 31,
        "timeout": 11,
        "server_settings": {"idle_in_transaction_session_timeout": "65000"},
    }
