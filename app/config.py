"""
Application configuration.

All runtime parameters live here. Loaded from environment variables
(via pydantic-settings) and validated fail-fast on startup.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config_groups import (
    ApiSettings,
    AppEnv,
    AppRole,
    AppSettings,
    BackupSettings,
    CircuitBreakerSettings,
    CleanupSettings,
    DatabaseSettings,
    DeliverySettings,
    InstantDownloadSettings,
    MetricsSettings,
    ProgressSettings,
    RateLimitSettings,
    RedisSettings,
    SentrySettings,
    StorageSettings,
    TelegramSettings,
    ToolingSettings,
    UpstreamSettings,
    WorkerSettings,
)
from app.domain.rate_limit import LimitLayer, LimitWindow

__all__ = [
    "AppEnv",
    "AppRole",
    "RateLimitWindows",
    "Settings",
    "get_settings",
    "parse_rl_window",
]


@dataclass(frozen=True, slots=True)
class RateLimitWindows:
    """Bundle of parsed RL_* windows. Cheap to construct."""

    user_burst: LimitWindow
    user_hourly: LimitWindow
    chat_burst: LimitWindow
    global_burst: LimitWindow
    domain_burst: LimitWindow


def parse_rl_window(raw: str, *, layer: LimitLayer) -> LimitWindow:
    """Parse the ``"<limit>/<window_s>"`` notation used by ``RL_*`` env vars.

    Lenient on whitespace, strict on shape. Raises ``ValueError`` with a
    message that names the offending env var family (``layer``).
    """
    text = (raw or "").strip()
    if "/" not in text:
        raise ValueError(
            f"RL window for {layer.value!r} must look like '<limit>/<seconds>', got {raw!r}"
        )
    left, right = text.split("/", 1)
    try:
        limit = int(left.strip())
        window_s = int(right.strip())
    except ValueError as exc:
        raise ValueError(f"RL window for {layer.value!r} has non-integer parts: {raw!r}") from exc
    return LimitWindow(layer=layer, limit=limit, window_s=window_s)


class Settings(
    AppSettings,
    TelegramSettings,
    DatabaseSettings,
    RedisSettings,
    StorageSettings,
    DeliverySettings,
    ApiSettings,
    WorkerSettings,
    CleanupSettings,
    InstantDownloadSettings,
    ProgressSettings,
    BackupSettings,
    ToolingSettings,
    UpstreamSettings,
    SentrySettings,
    CircuitBreakerSettings,
    MetricsSettings,
    RateLimitSettings,
    BaseSettings,
):
    """Top-level settings. Validated at process startup.

    Fields are declared per topic in ``app/config_groups.py``; env names stay flat.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- Derived ----------

    @property
    def is_production(self) -> bool:
        return self.APP_ENV is AppEnv.PRODUCTION

    @property
    def telegram_max_upload_bytes(self) -> int:
        return self.TELEGRAM_MAX_UPLOAD_MB * 1024 * 1024

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024

    @property
    def admin_ids(self) -> set[int]:
        if not self.BOT_ADMIN_IDS:
            return set()
        return {int(x.strip()) for x in self.BOT_ADMIN_IDS.split(",") if x.strip()}

    @property
    def database_url(self) -> str:
        # When DATABASE_URL is set explicitly the operator is responsible
        # for percent-encoding the password themselves — we cannot know
        # whether characters like ``@`` or ``:`` are intended separators
        # or literal bytes inside a pre-built DSN.
        if self.DATABASE_URL:
            return self.DATABASE_URL
        # Audit fix A8: compose the DSN with percent-encoded credentials
        # so a password containing URI-reserved characters (``@``, ``:``,
        # ``/``, ``?``, ``#``, ``%``, etc.) doesn't corrupt the parse.
        # ``quote`` with ``safe=""`` escapes *all* reserved bytes incl.
        # the forward slash. The username gets the same treatment for
        # consistency, even though ``POSTGRES_USER`` is admin-controlled
        # and unlikely to contain exotic characters.
        user = quote(self.POSTGRES_USER, safe="")
        password = quote(self.POSTGRES_PASSWORD, safe="")
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def database_url_sync(self) -> str:
        """Sync URL for Alembic."""
        return self.database_url.replace("+asyncpg", "+psycopg")

    @property
    def rate_limit_windows(self) -> RateLimitWindows:
        """Parsed rate-limit windows. Raises ValueError if any RL_* is malformed."""
        return RateLimitWindows(
            user_burst=parse_rl_window(self.RL_USER_BURST, layer=LimitLayer.USER_BURST),
            user_hourly=parse_rl_window(self.RL_USER_HOURLY, layer=LimitLayer.USER_HOURLY),
            chat_burst=parse_rl_window(self.RL_CHAT_BURST, layer=LimitLayer.CHAT_BURST),
            global_burst=parse_rl_window(self.RL_GLOBAL_BURST, layer=LimitLayer.GLOBAL_BURST),
            domain_burst=parse_rl_window(self.RL_DOMAIN_BURST, layer=LimitLayer.DOMAIN_BURST),
        )

    @property
    def redis_url(self) -> str:
        if self.REDIS_URL:
            return self.REDIS_URL
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ---------- Validators ----------

    @field_validator("LOG_LEVEL")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}")
        return upper

    @field_validator("PUBLIC_BASE_URL")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("PUBLIC_BASE_URL must start with http:// or https://")
        return v.rstrip("/")

    # ---------- Runtime fail-fast ----------

    def validate_runtime(self, *, require_storage: bool, require_tools: bool) -> list[str]:
        """
        Validate the runtime environment. Returns a list of human-readable errors.
        Caller must abort startup if non-empty.
        """
        errors: list[str] = []

        if require_storage:
            for path in (self.STORAGE_PATH, self.STORAGE_TMP_PATH):
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    test = path / ".write-test"
                    test.write_text("ok", encoding="utf-8")
                    test.unlink(missing_ok=True)
                except OSError as exc:
                    errors.append(f"Storage path {path} not writable: {exc}")

        if require_tools:
            for bin_name in (self.FFMPEG_BIN, self.YTDLP_BIN):
                if shutil.which(bin_name) is None:
                    errors.append(f"Required binary not found in PATH: {bin_name}")

        if self.is_production and not self.API_INTERNAL_TOKEN:
            errors.append("API_INTERNAL_TOKEN must be set in production")

        if self.is_production and self.PUBLIC_BASE_URL.startswith("http://"):
            errors.append("PUBLIC_BASE_URL must be HTTPS in production")

        if self.is_production and self.INTERNAL_TEST_TOKEN:
            errors.append(
                "INTERNAL_TEST_TOKEN must be empty in production "
                "(it gates a dev-only test-enqueue endpoint)"
            )

        min_orphan_age = self.JOB_TIMEOUT_SECONDS * 1.5
        if min_orphan_age > self.ORPHAN_JOB_AGE_SECONDS:
            errors.append(
                "ORPHAN_JOB_AGE_SECONDS "
                f"({self.ORPHAN_JOB_AGE_SECONDS}) must be >= "
                f"JOB_TIMEOUT_SECONDS * 1.5 ({min_orphan_age:.0f}) "
                "to avoid reaping live jobs."
            )

        errors.extend(self._validate_rate_limit_runtime())
        errors.extend(self._validate_metrics_runtime())

        return errors

    def _validate_metrics_runtime(self) -> list[str]:
        """Refuse to expose /metrics on a public bind in production.

        Public ``/metrics`` leaks operational signal (queue depth, error
        rates, deny patterns). docs/35- §7 explicitly mandates a private
        port; ADR-0006 records the in-process exporter decision.
        """
        if not self.METRICS_ENABLED:
            return []
        errors: list[str] = []
        if self.is_production and self.METRICS_BIND_HOST in ("0.0.0.0", "::"):
            errors.append(
                f"METRICS_BIND_HOST={self.METRICS_BIND_HOST!r} in production "
                "exposes /metrics publicly. Bind to 127.0.0.1 (and proxy via "
                "the private network) or document the exception with an ADR."
            )
        return errors

    def _validate_rate_limit_runtime(self) -> list[str]:
        """Cross-layer RL_* checks. Extracted to keep ``validate_runtime`` flat.

        Catches the "common mistake #6" hierarchy bug from docs/36- §10
        and the production fail-closed footgun from §3.4.
        """
        errors: list[str] = []
        try:
            rl = self.rate_limit_windows
        except ValueError as exc:
            errors.append(f"Invalid RL_* configuration: {exc}")
            return errors

        if rl.user_burst.limit > rl.chat_burst.limit:
            errors.append(
                f"RL_USER_BURST limit ({rl.user_burst.limit}) must be <= "
                f"RL_CHAT_BURST limit ({rl.chat_burst.limit})"
            )
        if rl.chat_burst.limit > rl.global_burst.limit:
            errors.append(
                f"RL_CHAT_BURST limit ({rl.chat_burst.limit}) must be <= "
                f"RL_GLOBAL_BURST limit ({rl.global_burst.limit})"
            )
        if rl.user_burst.window_s > rl.user_hourly.window_s:
            errors.append(
                f"RL_USER_BURST window ({rl.user_burst.window_s}s) must be <= "
                f"RL_USER_HOURLY window ({rl.user_hourly.window_s}s)"
            )
        if self.is_production and not self.RL_FAIL_OPEN:
            errors.append(
                "RL_FAIL_OPEN=false in production requires an ADR (docs/36- §3.4); "
                "set it back to true or document the exception."
            )
        return errors


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — single source of truth."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        # Re-raise with a clearer message; the caller logs and exits.
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
