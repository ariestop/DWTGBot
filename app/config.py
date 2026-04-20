"""
Application configuration.

All runtime parameters live here. Loaded from environment variables
(via pydantic-settings) and validated fail-fast on startup.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.rate_limit import LimitLayer, LimitWindow


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


class AppEnv(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class AppRole(str, Enum):
    BOT = "bot"
    API = "api"
    WORKER = "worker"
    ALL = "all"


class Settings(BaseSettings):
    """Top-level settings. Validated at process startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    APP_ROLE: AppRole = AppRole.ALL
    APP_ENV: AppEnv = AppEnv.DEVELOPMENT
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    # --- Telegram ---
    BOT_TOKEN: str = Field(..., min_length=10)
    BOT_ADMIN_IDS: str = ""
    TELEGRAM_MAX_UPLOAD_MB: int = Field(49, ge=1, le=2000)

    # --- Database ---
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "dwtgbot"
    POSTGRES_USER: str = "dwtgbot"
    POSTGRES_PASSWORD: str = ""
    DATABASE_URL: str = ""
    # S5: SQLAlchemy AsyncEngine pool sizing. Defaults are tuned for the
    # bot/api processes (mostly idle, occasional bursts). Worker should
    # raise these via env when WORKER_CONCURRENCY > 5 to avoid pool
    # exhaustion under load — see deploy/nl2/.env.example.
    DB_POOL_SIZE: int = Field(5, ge=1, le=200)
    DB_MAX_OVERFLOW: int = Field(10, ge=0, le=200)
    DB_POOL_TIMEOUT_S: float = Field(30.0, ge=0.5, le=300.0)
    DB_POOL_RECYCLE_S: int = Field(1800, ge=60)

    # --- Redis ---
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0
    REDIS_URL: str = ""

    # --- Storage ---
    STORAGE_PATH: Path = Path("/var/lib/dwtgbot/storage")
    STORAGE_TMP_PATH: Path = Path("/var/lib/dwtgbot/tmp")
    MAX_FILE_SIZE_MB: int = Field(2048, ge=1)
    # S10: minimum free space on STORAGE_PATH to accept new jobs / start
    # a download. ``0`` disables the guard. The check is best-effort
    # (statvfs); on a full filesystem we fail enqueue with TooManyJobsError
    # and skip the download cycle in the worker (see LocalStorage.assert_free_space).
    STORAGE_MIN_FREE_MB: int = Field(0, ge=0)

    # --- Public delivery ---
    PUBLIC_BASE_URL: str = "http://localhost:8080"
    TEMP_LINK_TTL_SECONDS: int = Field(86_400, ge=60)
    TEMP_LINK_MAX_DOWNLOADS: int = Field(5, ge=1, le=1000)
    TEMP_LINK_TOKEN_BYTES: int = Field(32, ge=16, le=128)

    # --- Internal API ---
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8080
    API_INTERNAL_TOKEN: str = ""
    # S6 (audit fix): trust gate for the ``X-Internal-XAccel`` header
    # that switches /d/{token} into nginx X-Accel-Redirect mode.
    # Default: OFF — the api streams the file itself. Flip to ``true``
    # only when running behind the bundled nginx config that
    # *unconditionally* sets the header (deploy/nginx/conf.d/media.conf
    # — see S6 comment there). Enabling without nginx in front lets a
    # client supply the header and reach the protected file directly.
    XACCEL_ENABLED: bool = False
    # Dev/CI-only test enqueue endpoint. MUST be empty in production
    # (validated by ``Settings.validate_runtime`` — see app/config.py).
    INTERNAL_TEST_TOKEN: str = ""
    # NL-1 control-plane API serves only internal /healthz; it does not
    # stream downloads from disk. Set ``false`` on NL-1 (same idea as
    # ``main_bot`` using ``require_storage=False``). NL-2 media-plane API
    # MUST keep the default ``true`` so misconfigured storage fails fast.
    API_VALIDATE_STORAGE: bool = True

    # --- Worker / queue ---
    WORKER_CONCURRENCY: int = Field(2, ge=1, le=64)
    JOB_TIMEOUT_SECONDS: int = Field(1800, ge=30)
    JOB_MAX_RETRIES: int = Field(2, ge=0, le=10)
    DOWNLOAD_TIMEOUT_SECONDS: int = Field(900, ge=30)
    MAX_CONCURRENT_JOBS_PER_USER: int = Field(2, ge=1, le=100)

    # --- Cleanup / cache ---
    CLEANUP_INTERVAL_SECONDS: int = Field(3600, ge=60)
    MEDIA_CACHE_TTL_SECONDS: int = Field(21_600, ge=60)
    # S1 (audit fix): threshold for marking PROCESSING jobs as orphaned.
    # MUST exceed JOB_TIMEOUT_SECONDS by a comfortable margin so a
    # legitimately-slow job (e.g. 4K re-encode) is never reaped while
    # still running. Default = 2 * JOB_TIMEOUT default + 5 min slack.
    ORPHAN_JOB_AGE_SECONDS: int = Field(3900, ge=60)

    # --- Backup ---
    BACKUP_DIR: Path = Path("/var/backups/dwtgbot")
    BACKUP_RETENTION_DAYS: int = Field(14, ge=1)

    # --- Tooling ---
    FFMPEG_BIN: str = "ffmpeg"
    YTDLP_BIN: str = "yt-dlp"

    # --- Outbound proxy (S9) ---
    # Optional HTTP(S)/SOCKS5 proxy for yt-dlp upstream calls. Empty
    # disables the feature; otherwise the URL is passed verbatim into
    # yt-dlp ``proxy`` opt and into HTTPX clients (DEFAULT for outbound
    # calls). Format: ``http://user:pass@host:port`` or
    # ``socks5h://host:port``.
    HTTPS_PROXY_URL: str = ""

    # --- Sentry (L7) ---
    # Error aggregation. Blank DSN disables the integration entirely
    # (``configure_sentry`` is a no-op). Set DSN to the project URL
    # from Sentry / GlitchTip; environment falls back to APP_ENV.
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = ""
    # 0.0 = do not sample traces; bump to e.g. 0.1 to trace 10% of
    # transactions. Keep off in prod until the budget is understood.
    SENTRY_TRACES_SAMPLE_RATE: float = Field(0.0, ge=0.0, le=1.0)
    # Optional release marker (usually injected by CI with a git SHA
    # or tag). Blank leaves Sentry to auto-derive from ``SENTRY_RELEASE``
    # or fall back to "unknown".
    SENTRY_RELEASE: str = ""

    # --- Circuit breaker for yt-dlp upstream (L5) ---
    # Tracked per upstream host in Redis so every worker in the fleet
    # sees the same "open" / "closed" signal. Defaults are conservative:
    # 5 consecutive 429/throttle responses within 60 s open the
    # breaker for 5 minutes. Flip CB_ENABLED=false for environments
    # without a shared Redis (e.g. local single-process dev).
    CB_ENABLED: bool = True
    CB_FAILURE_THRESHOLD: int = Field(5, ge=1)
    CB_WINDOW_SECONDS: int = Field(60, ge=5)
    CB_COOLDOWN_SECONDS: int = Field(300, ge=10)

    # --- Metrics / observability (docs/35-metrics-and-slo.md §7, ADR-0006) ---
    # Master switch. ``false`` keeps the bot lean and skips the /metrics
    # server entirely (Noop sink). Flip to ``true`` only after wiring a
    # private port through the Docker network — see deploy/nl1/.env.example.
    METRICS_ENABLED: bool = False
    # Default to loopback so a misconfigured deploy never accidentally
    # exposes /metrics on the public interface. Production refuses 0.0.0.0
    # via ``Settings.validate_runtime`` (see ADR-0006 + docs/35- §7).
    METRICS_BIND_HOST: str = "127.0.0.1"
    METRICS_PORT: int = Field(9090, ge=1, le=65535)
    # Whether ``rate_limit_first_deny_total`` carries a ``user_id`` label.
    # ON for small deployments (~hundreds of active users); OFF for any
    # public-scale rollout — cardinality blows up Prometheus memory.
    # Either way, the structured log event is always emitted (LogQL works).
    METRICS_USER_ID_LABEL: bool = False
    # How often the bot samples ``arq_queue_depth`` via Redis ZCARD
    # (ADR-0007 §2.6). 15 s gives 20 samples per 5-min SLO B3 window
    # while keeping the Redis load negligible. Lower bound enforces a
    # sane minimum — sub-second sampling would DoS Redis under load.
    METRICS_QUEUE_SAMPLE_INTERVAL_S: float = Field(15.0, ge=1.0, le=300.0)

    # --- Rate limiting (docs/36-rate-limiting.md) ---
    # Master switch. ``false`` is the safe default — limiter is wired but
    # short-circuits to "allow" until you flip it on per the §11 rollout.
    RL_ENABLED: bool = False
    # ``"<limit>/<window_seconds>"`` notation, parsed by ``parse_rl_window``.
    RL_USER_BURST: str = "5/60"
    RL_USER_HOURLY: str = "20/3600"
    RL_CHAT_BURST: str = "15/60"
    RL_GLOBAL_BURST: str = "60/60"
    RL_DOMAIN_BURST: str = "30/60"
    # Per-user "told already" TTL — silences follow-up replies (§4.3).
    RL_NOTICE_TTL: int = Field(30, ge=1, le=3600)
    # Per-chat (group) notice TTL (§4.4).
    RL_CHAT_NOTICE_TTL: int = Field(30, ge=1, le=3600)
    # Fail-open is the project default; never flip without an ADR (§3.4).
    RL_FAIL_OPEN: bool = True

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
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
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
