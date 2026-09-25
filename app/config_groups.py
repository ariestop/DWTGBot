"""Field groups of :class:`app.config.Settings`.

Each group is a plain ``BaseModel`` mixin; ``Settings`` inherits all of
them, so every field keeps its flat environment variable name
(``POSTGRES_HOST``, not ``DATABASE__HOST``) and existing ``.env`` files
keep working. Loading, derived properties and runtime validation stay in
``app/config.py``; ``app/tests/test_config_groups.py`` checks that every
field belongs to exactly one group.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class AppEnv(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class AppRole(str, Enum):
    BOT = "bot"
    API = "api"
    WORKER = "worker"
    ALL = "all"


class AppSettings(BaseModel):
    """Role, environment and logging."""

    APP_ROLE: AppRole = AppRole.ALL
    APP_ENV: AppEnv = AppEnv.DEVELOPMENT
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False


class TelegramSettings(BaseModel):
    """Bot token, admins and upload limit."""

    BOT_TOKEN: str = Field(..., min_length=10)
    BOT_ADMIN_IDS: str = ""
    TELEGRAM_MAX_UPLOAD_MB: int = Field(49, ge=1, le=2000)


class DatabaseSettings(BaseModel):
    """Postgres connection and SQLAlchemy pool."""

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
    DB_STATEMENT_TIMEOUT_S: int = Field(30, ge=1, le=300)
    DB_CONNECT_TIMEOUT_S: int = Field(10, ge=1, le=120)
    DB_IDLE_IN_TX_TIMEOUT_MS: int = Field(60_000, ge=1000, le=3_600_000)


class RedisSettings(BaseModel):
    """Redis connection (queue, state, progress, rate limits)."""

    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0
    REDIS_URL: str = ""


class StorageSettings(BaseModel):
    """STORAGE_PATH layout and disk caps."""

    STORAGE_PATH: Path = Path("/var/lib/dwtgbot/storage")
    STORAGE_TMP_PATH: Path = Path("/var/lib/dwtgbot/tmp")
    MAX_FILE_SIZE_MB: int = Field(2048, ge=1)
    # S10: minimum free space on STORAGE_PATH to accept new jobs / start
    # a download. ``0`` disables the guard. The check is best-effort
    # (statvfs); on a full filesystem we fail enqueue with TooManyJobsError
    # and skip the download cycle in the worker (see LocalStorage.assert_free_space).
    STORAGE_MIN_FREE_MB: int = Field(0, ge=0)


class DeliverySettings(BaseModel):
    """Temp-link delivery (ADR-0004)."""

    PUBLIC_BASE_URL: str = "http://localhost:8080"
    TEMP_LINK_TTL_SECONDS: int = Field(3_600, ge=60)
    TEMP_LINK_MAX_DOWNLOADS: int = Field(5, ge=1, le=1000)
    TEMP_LINK_TOKEN_BYTES: int = Field(32, ge=16, le=128)


class ApiSettings(BaseModel):
    """FastAPI bind, internal tokens, X-Accel trust gate."""

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


class WorkerSettings(BaseModel):
    """arq worker concurrency, timeouts and per-user cap."""

    WORKER_CONCURRENCY: int = Field(2, ge=1, le=64)
    JOB_TIMEOUT_SECONDS: int = Field(1800, ge=30)
    JOB_MAX_RETRIES: int = Field(2, ge=0, le=10)
    DOWNLOAD_TIMEOUT_SECONDS: int = Field(900, ge=30)
    MAX_CONCURRENT_JOBS_PER_USER: int = Field(2, ge=1, le=100)


class CleanupSettings(BaseModel):
    """Cleanup loop, media cache and orphan reaper."""

    CLEANUP_INTERVAL_SECONDS: int = Field(3600, ge=60)
    MEDIA_CACHE_TTL_SECONDS: int = Field(21_600, ge=60)
    # S1 (audit fix): threshold for marking PROCESSING jobs as orphaned.
    # MUST exceed JOB_TIMEOUT_SECONDS by a comfortable margin so a
    # legitimately-slow job (e.g. 4K re-encode) is never reaped while
    # still running. Default = 2 * JOB_TIMEOUT default + 5 min slack.
    ORPHAN_JOB_AGE_SECONDS: int = Field(3900, ge=60)


class InstantDownloadSettings(BaseModel):
    """Instant-download UX, cancel flag and post-text button (ADR-0010 §2.1, §2.3)."""

    # Master switch for the auto-enqueue + live-progress UX. When
    # ``true`` (default), a URL message triggers immediate download
    # without the quality picker; when ``false`` the legacy picker
    # flow runs 1-to-1 (the flag is load-bearing for rollback).
    INSTANT_DOWNLOAD_ENABLED: bool = True
    # Appended verbatim after the final media caption (two newlines
    # separator). Empty string disables the footer entirely — tests
    # and dev deployments often want a clean caption.
    BRAND_FOOTER: str = "Спасибо за использование нашего бота @dwtgbot"
    # TTL on ``cancel:{job_id}`` flag. Must exceed JOB_TIMEOUT_SECONDS
    # so a cancel that races a slow job is still honoured on the final
    # phase-boundary check. 30 min matches ``PROGRESS_META_TTL_SEC``.
    CANCEL_FLAG_TTL_SEC: int = Field(1800, ge=60, le=7200)
    # Lifetime of the ``post_text:{job_id}`` Redis string populated by
    # the auto-enqueue flow. 24h is the product choice (ADR-0010 §2.3):
    # long enough that a user can come back to a forwarded video the
    # next day, short enough that an abandoned job does not pin
    # arbitrary text in Redis forever.
    POST_TEXT_TTL_SEC: int = Field(86_400, ge=600, le=604_800)
    # Minimum ``len(description.strip())`` before we bother persisting
    # the post text and rendering the button. Anything shorter is
    # likely noise (e.g. "#shorts") and rendering the button would
    # just let the user tap into an empty reply.
    POST_TEXT_MIN_CHARS: int = Field(10, ge=1, le=1000)
    # Upper bound for ``MediaInfo.description`` characters. Providers MUST
    # truncate to this value (see ADR-0010 §2.3). 10k chars is enough for
    # realistic YouTube descriptions / Instagram captions and still fits
    # in a few Telegram messages when chunked.
    POST_TEXT_MAX_CHARS: int = Field(10_000, ge=100, le=100_000)


class ProgressSettings(BaseModel):
    """Live-progress side channel (ADR-0010 §2.2)."""

    # TTL on progress:{job_id} hash. 10 min is well above the worst-case
    # job time (JOB_TIMEOUT_SECONDS) but small enough that orphaned
    # keys after a worker crash disappear quickly.
    PROGRESS_TTL_SEC: int = Field(600, ge=60, le=3600)
    # TTL on progress_meta:{job_id} (chat_id, message_id, started_at).
    # Bot uses these for recovery on restart; must exceed PROGRESS_TTL_SEC
    # so a restarting bot still sees the meta after a brief reporting gap.
    PROGRESS_META_TTL_SEC: int = Field(1800, ge=60, le=7200)
    # Per-job throttle in the reporter: drop updates closer together than
    # this many seconds when percent delta is below ``PROGRESS_DEBOUNCE_PERCENT``
    # AND stage has not changed. Terminal stages always pass through.
    PROGRESS_REDRAW_INTERVAL_SEC: float = Field(2.0, ge=0.1, le=30.0)
    PROGRESS_DEBOUNCE_PERCENT: int = Field(3, ge=0, le=50)
    # ADR-0010 §6 "Watchdog": bot-side updater replaces the placeholder
    # caption with a «connection lost» notice after this many seconds
    # without a publish event for an active job. Must stay well under
    # PROGRESS_META_TTL_SEC so the user sees feedback long before the
    # recovery scan would drop the job.
    #
    # Bumped 30s → 90s: yt-dlp's ``progress_hooks`` only fire for the
    # "downloading" status, so HLS fragment boundaries and postprocess
    # merge on Instagram/other platforms produce routine 30-60s silent
    # gaps that used to trigger the scary "connection lost" caption for
    # a perfectly healthy worker. 90s is still tight enough to catch a
    # real crash inside the user's patience window.
    PROGRESS_STALE_WARN_SEC: int = Field(90, ge=5, le=600)
    # Hard drop after this many seconds with no event — the placeholder
    # is removed entirely so the chat does not show a zombie progress
    # message after a worker crash. Must exceed ``PROGRESS_STALE_WARN_SEC``.
    PROGRESS_STALE_DROP_SEC: int = Field(300, ge=30, le=3600)


class BackupSettings(BaseModel):
    """pg_dump backups."""

    BACKUP_DIR: Path = Path("/var/backups/dwtgbot")
    BACKUP_RETENTION_DAYS: int = Field(14, ge=1)


class ToolingSettings(BaseModel):
    """External binaries."""

    FFMPEG_BIN: str = "ffmpeg"
    FFPROBE_BIN: str = "ffprobe"
    YTDLP_BIN: str = "yt-dlp"


class UpstreamSettings(BaseModel):
    """Outbound proxy and per-platform cookies for yt-dlp."""

    # Optional HTTP(S)/SOCKS5 proxy for yt-dlp upstream calls. Empty
    # disables the feature; otherwise the URL is passed verbatim into
    # yt-dlp ``proxy`` opt and into HTTPX clients (DEFAULT for outbound
    # calls). Format: ``http://user:pass@host:port`` or
    # ``socks5h://host:port``.
    HTTPS_PROXY_URL: str = ""
    # Optional path to a Netscape cookies.txt file for YouTube.
    # When set, the YouTube provider passes it as yt-dlp ``cookiefile``
    # for metadata probe + download calls.
    YOUTUBE_COOKIES_FILE: str = ""
    # Optional path to a Netscape cookies.txt file for Instagram.
    # When set, the Instagram provider passes it as yt-dlp ``cookiefile``
    # for metadata probe + download calls.
    INSTAGRAM_COOKIES_FILE: str = ""


class SentrySettings(BaseModel):
    """Error aggregation (L7)."""

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


class CircuitBreakerSettings(BaseModel):
    """Per-host circuit breaker for yt-dlp upstream (L5)."""

    # Tracked per upstream host in Redis so every worker in the fleet
    # sees the same "open" / "closed" signal. Defaults are conservative:
    # 5 consecutive 429/throttle responses within 60 s open the
    # breaker for 5 minutes. Flip CB_ENABLED=false for environments
    # without a shared Redis (e.g. local single-process dev).
    CB_ENABLED: bool = True
    CB_FAILURE_THRESHOLD: int = Field(5, ge=1)
    CB_WINDOW_SECONDS: int = Field(60, ge=5)
    CB_COOLDOWN_SECONDS: int = Field(300, ge=10)


class MetricsSettings(BaseModel):
    """Prometheus exporters (docs/35-metrics-and-slo.md §7, ADR-0006)."""

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


class RateLimitSettings(BaseModel):
    """Rate limiting (docs/36-rate-limiting.md)."""

    # Master switch. Audit fix A11: default flipped to ``True`` so a
    # fresh deploy ships with rate-limiting ON. Public Telegram bots
    # are an easy DoS target (one viral link, one malicious sender
    # flood) and ``False`` by default left the service exposed when
    # the operator forgot to turn RL on. Disabling is still possible
    # and is expected to be documented via an ADR per docs/17-security.md.
    RL_ENABLED: bool = True
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


SETTINGS_GROUPS: tuple[type[BaseModel], ...] = (
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
)
