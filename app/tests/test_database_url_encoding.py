"""Audit fix A8: ``Settings.database_url`` percent-encodes credentials.

A Postgres password containing any URI-reserved byte (``@``, ``:``,
``/``, ``?``, ``#``, ``%``) used to corrupt the DSN — asyncpg either
failed to parse or bound the password to the wrong field. Encoding at
the source keeps operators free to pick strong random passwords.
"""

from __future__ import annotations

from app.config import Settings


def test_special_characters_in_password_are_percent_encoded() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        POSTGRES_USER="dwtgbot",
        POSTGRES_PASSWORD="p@ss:word/!#?%",
        POSTGRES_HOST="db.internal",
        POSTGRES_PORT=5432,
        POSTGRES_DB="dwtgbot",
    )

    dsn = settings.database_url
    # None of the raw reserved characters leak into the DSN past the
    # scheme authority delimiter.
    assert "p@ss" not in dsn.split("@", 2)[0] + "@"  # literal @ in password would confuse split
    assert "p%40ss" in dsn
    assert "%3A" in dsn  # `:` → %3A
    assert "%2F" in dsn  # `/` → %2F
    assert "%21" in dsn  # `!` → %21
    assert "%23" in dsn  # `#` → %23
    assert "%3F" in dsn  # `?` → %3F
    assert "%25" in dsn  # `%` → %25
    # Host/port/db stay literal — only user & password are encoded.
    assert "@db.internal:5432/dwtgbot" in dsn


def test_plain_password_is_unchanged() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        POSTGRES_USER="dwtgbot",
        POSTGRES_PASSWORD="simple_password_123",
        POSTGRES_HOST="db",
        POSTGRES_PORT=5432,
        POSTGRES_DB="dwtgbot",
    )
    assert (
        settings.database_url == "postgresql+asyncpg://dwtgbot:simple_password_123@db:5432/dwtgbot"
    )


def test_explicit_database_url_passes_through_unchanged() -> None:
    explicit = "postgresql+asyncpg://alice:secret@custom.host/mydb"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        DATABASE_URL=explicit,
    )
    assert settings.database_url == explicit


def test_sync_variant_swaps_driver() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        POSTGRES_USER="dwtgbot",
        POSTGRES_PASSWORD="p@ss",
        POSTGRES_HOST="db",
        POSTGRES_PORT=5432,
        POSTGRES_DB="dwtgbot",
    )
    assert settings.database_url_sync.startswith("postgresql+psycopg://")
    assert "p%40ss" in settings.database_url_sync
