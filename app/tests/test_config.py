"""Settings construction + validation."""

from __future__ import annotations

import pytest

from app.config import AppEnv, get_settings


def test_loads_with_minimum_env() -> None:
    s = get_settings()
    assert s.BOT_TOKEN.startswith("0000000000:")
    assert s.APP_ENV is AppEnv.DEVELOPMENT
    assert s.telegram_max_upload_bytes == s.TELEGRAM_MAX_UPLOAD_MB * 1024 * 1024


def test_database_url_falls_back_to_components(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_HOST", "h")
    monkeypatch.setenv("POSTGRES_DB", "d")
    get_settings.cache_clear()
    s = get_settings()
    assert s.database_url == "postgresql+asyncpg://u:p@h:5432/d"


def test_log_level_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "info")
    get_settings.cache_clear()
    assert get_settings().LOG_LEVEL == "INFO"


def test_invalid_log_level_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "loud")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        get_settings()


def test_admin_ids_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_ADMIN_IDS", "1, 2 ,3,")
    get_settings.cache_clear()
    assert get_settings().admin_ids == {1, 2, 3}


def test_validate_runtime_storage_writable(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "s"))
    monkeypatch.setenv("STORAGE_TMP_PATH", str(tmp_path / "t"))
    get_settings.cache_clear()
    errors = get_settings().validate_runtime(require_storage=True, require_tools=False)
    assert errors == []


def test_production_requires_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://example.com")
    monkeypatch.setenv("API_INTERNAL_TOKEN", "x")
    get_settings.cache_clear()
    errors = get_settings().validate_runtime(require_storage=False, require_tools=False)
    assert any("HTTPS" in e for e in errors)


def test_production_requires_internal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("API_INTERNAL_TOKEN", "")
    get_settings.cache_clear()
    errors = get_settings().validate_runtime(require_storage=False, require_tools=False)
    assert any("API_INTERNAL_TOKEN" in e for e in errors)


def _production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("API_INTERNAL_TOKEN", "x")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    monkeypatch.delenv("DEPLOY_TOPOLOGY", raising=False)


def _redis_errors(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    get_settings.cache_clear()
    errors = get_settings().validate_runtime(require_storage=False, require_tools=False)
    return [e for e in errors if "Redis must require a password" in e]


def test_production_requires_redis_password(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_env(monkeypatch)
    monkeypatch.setenv("REDIS_PASSWORD", "")
    assert _redis_errors(monkeypatch)


def test_production_accepts_redis_password(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_env(monkeypatch)
    monkeypatch.setenv("REDIS_PASSWORD", "s3cret")
    assert _redis_errors(monkeypatch) == []


def test_production_accepts_password_in_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://:s3cret@redis:6379/0")
    assert _redis_errors(monkeypatch) == []


def test_production_redis_url_without_password_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # REDIS_URL takes precedence over REDIS_PASSWORD in ``redis_url``, so a
    # password-less URL is unauthenticated even when REDIS_PASSWORD is set.
    _production_env(monkeypatch)
    monkeypatch.setenv("REDIS_PASSWORD", "s3cret")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    assert _redis_errors(monkeypatch)


def test_single_topology_allows_redis_without_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ADR-0011 single host: Redis publishes no port, only app containers
    # on dwtgbot_internal reach it.
    _production_env(monkeypatch)
    monkeypatch.setenv("DEPLOY_TOPOLOGY", "single")
    assert _redis_errors(monkeypatch) == []


def test_split_topology_requires_redis_password(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_env(monkeypatch)
    monkeypatch.setenv("DEPLOY_TOPOLOGY", "split")
    assert _redis_errors(monkeypatch)


def test_dev_allows_redis_without_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_PASSWORD", "")
    assert _redis_errors(monkeypatch) == []


def test_production_rejects_internal_test_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("API_INTERNAL_TOKEN", "x")
    monkeypatch.setenv("INTERNAL_TEST_TOKEN", "leaked-into-prod")
    get_settings.cache_clear()
    errors = get_settings().validate_runtime(require_storage=False, require_tools=False)
    assert any("INTERNAL_TEST_TOKEN" in e for e in errors)


def test_orphan_age_must_exceed_job_timeout_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("API_INTERNAL_TOKEN", "x")
    monkeypatch.setenv("JOB_TIMEOUT_SECONDS", "1800")
    monkeypatch.setenv("ORPHAN_JOB_AGE_SECONDS", "2000")
    get_settings.cache_clear()

    errors = get_settings().validate_runtime(require_storage=False, require_tools=False)

    assert any("ORPHAN_JOB_AGE_SECONDS" in e for e in errors)


def test_orphan_pending_age_default_and_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    assert get_settings().ORPHAN_PENDING_AGE_SECONDS == 14_400

    monkeypatch.setenv("ORPHAN_PENDING_AGE_SECONDS", "60")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="ORPHAN_PENDING_AGE_SECONDS"):
        get_settings()


def test_max_concurrent_jobs_default() -> None:
    s = get_settings()
    assert s.MAX_CONCURRENT_JOBS_PER_USER == 2


def test_max_concurrent_jobs_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_CONCURRENT_JOBS_PER_USER", "5")
    get_settings.cache_clear()
    assert get_settings().MAX_CONCURRENT_JOBS_PER_USER == 5


def test_instagram_cookies_file_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSTAGRAM_COOKIES_FILE", raising=False)
    get_settings.cache_clear()
    assert get_settings().INSTAGRAM_COOKIES_FILE == ""

    monkeypatch.setenv("INSTAGRAM_COOKIES_FILE", "/tmp/ig.cookies.txt")
    get_settings.cache_clear()
    assert get_settings().INSTAGRAM_COOKIES_FILE == "/tmp/ig.cookies.txt"


def test_youtube_cookies_file_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YOUTUBE_COOKIES_FILE", raising=False)
    get_settings.cache_clear()
    assert get_settings().YOUTUBE_COOKIES_FILE == ""

    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", "/tmp/yt.cookies.txt")
    get_settings.cache_clear()
    assert get_settings().YOUTUBE_COOKIES_FILE == "/tmp/yt.cookies.txt"
