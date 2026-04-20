# 13 — Configuration and environment

> Status: Stable
> Audience: SRE / DevOps, backend engineers, AI agents adding settings
> Read next: [`17-security.md`](17-security.md), [`19-docker-architecture.md`](19-docker-architecture.md)

All runtime configuration lives in `app/config.py` as a single
`pydantic-settings` model. Values come from environment variables (loaded
from `.env` in development, from Docker `env_file` in production) and are
validated at process startup. **No hidden config files. No global
mutables. No "set it later".**

---

## 1. The config object

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
```

- `extra="ignore"` — unknown env vars don't crash the process; they are
  simply ignored. (You may surface them via `extra="forbid"` in tests if
  you want strict drift detection.)
- `case_sensitive=False` — `BOT_TOKEN`, `bot_token` and `Bot_Token` all
  resolve to the same field.
- `env_file=".env"` — convenience for local dev only. In production, env
  vars are injected by Docker Compose via `env_file: .env` (per stack).

Single accessor:

```python
@lru_cache(maxsize=1)
def get_settings() -> Settings: ...
```

Cached. Re-importing won't reload `.env` mid-process. Tests patch via
`monkeypatch.setenv(...)` and call `get_settings.cache_clear()` (see
`app/tests/conftest.py`).

---

## 2. Variable reference

Grouped by concern. **Bold** = required, no safe default.

### App

| Variable | Type | Default | Notes |
|---|---|---|---|
| `APP_ROLE` | enum (`bot`/`api`/`worker`/`all`) | `all` | Diagnostic; the actual entrypoint determines behaviour |
| `APP_ENV` | enum (`development`/`production`) | `development` | Controls JSON logs, validation strictness |
| `LOG_LEVEL` | enum (`DEBUG`…`CRITICAL`) | `INFO` | Validated; case-insensitive |
| `LOG_JSON` | bool | `false` | In production JSON is forced regardless |

### Telegram

| Variable | Type | Default | Notes |
|---|---|---|---|
| **`BOT_TOKEN`** | str (≥10 chars) | — | Telegram bot API token. Treat as a secret |
| `BOT_ADMIN_IDS` | csv of int | empty | Allowed admin Telegram user ids |
| `TELEGRAM_MAX_UPLOAD_MB` | int 1..2000 | `49` | 50 MiB official; raise only with self-hosted Bot API |

### Database (Postgres)

| Variable | Type | Default | Notes |
|---|---|---|---|
| `POSTGRES_HOST` | str | `postgres` | Service name in NL-1; private IP from NL-2 |
| `POSTGRES_PORT` | int | `5432` | |
| `POSTGRES_DB` | str | `dwtgbot` | |
| `POSTGRES_USER` | str | `dwtgbot` | |
| **`POSTGRES_PASSWORD`** | str | empty | **Must be set in production** |
| `DATABASE_URL` | str | computed | Overrides the host/port/etc. composition. Use only when needed |
| `DB_POOL_SIZE` | int ≥1 | `5` | SQLAlchemy async pool size. S5 audit fix: was hard-coded. Raise on the worker container when `WORKER_CONCURRENCY > 5` (see `20-deployment.md` §7.1 — per-host values) |
| `DB_MAX_OVERFLOW` | int ≥0 | `10` | Extra connections created beyond `DB_POOL_SIZE` before clients block on `DB_POOL_TIMEOUT_S` |
| `DB_POOL_TIMEOUT_S` | int ≥1 | `30` | How long a caller waits for a free connection before `TimeoutError` |
| `DB_POOL_RECYCLE_S` | int ≥60 | `1800` | Recycle stale connections; safer than Postgres `idle_in_transaction_session_timeout` races |

> The async URL is `postgresql+asyncpg://...`; a sync version
> (`+psycopg`) is derived for Alembic.

### Redis

| Variable | Type | Default | Notes |
|---|---|---|---|
| `REDIS_HOST` | str | `redis` | Same dual-use pattern as Postgres |
| `REDIS_PORT` | int | `6379` | |
| `REDIS_PASSWORD` | str | empty | Set in production |
| `REDIS_DB` | int | `0` | |
| `REDIS_URL` | str | computed | Overrides if set |

### Storage

| Variable | Type | Default | Notes |
|---|---|---|---|
| `STORAGE_PATH` | path | `/var/lib/dwtgbot/storage` | Mounted as RW into worker, RO into nginx |
| `STORAGE_TMP_PATH` | path | `/var/lib/dwtgbot/tmp` | Worker scratch; **must be local SSD**, never NFS |
| `MAX_FILE_SIZE_MB` | int ≥1 | `2048` | Per-result hard cap; ≥ `TELEGRAM_MAX_UPLOAD_MB` always |
| `STORAGE_MIN_FREE_MB` | int ≥0 | `0` | S10 audit fix: disk-space backpressure. Worker calls `LocalStorage.assert_free_space()` at the top of `ProcessDownloadUseCase._run()`; below the threshold the job fails permanently with a user-friendly "сервис временно перегружен" message. `0` disables the guard. On NL-2 set to ~`3 × MAX_FILE_SIZE_MB` so a single cleanup miss can't push the FS to 100%. No-op on NL-1 (bot never writes to storage). |

### Public delivery (temp links)

| Variable | Type | Default | Notes |
|---|---|---|---|
| **`PUBLIC_BASE_URL`** | url | `http://localhost:8080` | `https://` required in production |
| `TEMP_LINK_TTL_SECONDS` | int ≥60 | `86400` (24 h) | Issue-time TTL |
| `TEMP_LINK_MAX_DOWNLOADS` | int 1..1000 | `5` | Per-link counter |
| `TEMP_LINK_TOKEN_BYTES` | int 16..128 | `32` | URL-safe-base64 length ≈ 4·N/3 |

### Internal API

| Variable | Type | Default | Notes |
|---|---|---|---|
| `API_HOST` | str | `0.0.0.0` | Bind address inside container |
| `API_PORT` | int | `8080` | Container-internal; never published |
| `API_INTERNAL_TOKEN` | str | empty | **Required in production** for internal admin endpoints |
| `INTERNAL_TEST_TOKEN` | str | empty | **MUST be empty in production.** Non-empty value enables `POST /internal/test/enqueue` for capacity tests (`docs/37-load-and-capacity.md` §6); rejected at startup when `APP_ENV=production` |
| `XACCEL_ENABLED` | bool | `false` | S6 audit fix: trust gate for nginx `X-Accel-Redirect` on `GET /api/v1/dl/{token}`. The api only emits `X-Accel-Redirect` when **both** the loopback `X-Internal-XAccel: 1` header (set unconditionally by `deploy/nginx/conf.d/media.conf.template`) *and* `XACCEL_ENABLED=true` are present. Flip to `true` only on hosts that actually have the bundled nginx in front of the api (NL-2). Leaving it `false` makes the api stream bytes itself — slower but safer if nginx is removed/misconfigured. |

### Worker / queue

| Variable | Type | Default | Notes |
|---|---|---|---|
| `WORKER_CONCURRENCY` | int 1..64 | `2` | Concurrent jobs per worker process |
| `JOB_TIMEOUT_SECONDS` | int ≥30 | `1800` (30 min) | Per-attempt cap; arq cancels coroutine |
| `JOB_MAX_RETRIES` | int 0..10 | `2` | Total attempts = `JOB_MAX_RETRIES + 1` |
| `DOWNLOAD_TIMEOUT_SECONDS` | int ≥30 | `900` | Provider-level safety cap (advisory) |
| `MAX_CONCURRENT_JOBS_PER_USER` | int 1..100 | `2` | Defensive cap enforced in `EnqueueDownloadUseCase`; counts jobs in `pending` + `processing`. Exceeding it raises `TooManyJobsError` and the user sees a friendly "wait for previous jobs" message. See `docs/37-load-and-capacity.md` §7 |

### Cleanup / cache

| Variable | Type | Default | Notes |
|---|---|---|---|
| `CLEANUP_INTERVAL_SECONDS` | int ≥60 | `3600` (1 h) | Cleanup container loop |
| `MEDIA_CACHE_TTL_SECONDS` | int ≥60 | `21600` (6 h) | Both metadata cache and Redis request state |
| `ORPHAN_JOB_AGE_SECONDS` | int ≥60 | `3900` | S1 audit fix: the cleanup worker marks any `download_jobs` row stuck in `PROCESSING` older than this as `FAILED` with reason `internal_error`, releasing the per-user cap. **Must be ≥ 2 × `JOB_TIMEOUT_SECONDS`** so in-flight long jobs are never reaped. Non-zero reap counts are logged at WARNING (`orphan_jobs_reaped`) — a persistent signal means a worker is dying mid-job. |

### Backup

| Variable | Type | Default | Notes |
|---|---|---|---|
| `BACKUP_DIR` | path | `/var/backups/dwtgbot` | Inside backup container; volume-mounted |
| `BACKUP_RETENTION_DAYS` | int ≥1 | `14` | Days of dumps kept |
| `BACKUP_INTERVAL_SECONDS` | int | `86400` | Set on the backup container env (compose) |
| `BACKUP_S3_BUCKET` | str | empty | S2 audit fix: off-site replication backend #1. When set, `deploy/scripts/backup.sh::replicate_offsite` uploads each fresh dump via `aws s3 cp`. Requires `awscli` (bundled in `docker/backup.Dockerfile`) + ambient AWS credentials (instance profile, env, or `~/.aws/credentials`). Leave empty to skip. |
| `BACKUP_S3_PREFIX` | str | `dwtgbot` | Object-key prefix inside `BACKUP_S3_BUCKET`. |
| `BACKUP_RCLONE_REMOTE` | str | empty | S2 audit fix: off-site replication backend #2. Any remote known to rclone (`rclone config`) — B2, GCS, SFTP, etc. Uploads via `rclone copyto`. `rclone` is bundled in `docker/backup.Dockerfile`. Pick exactly one of `BACKUP_S3_BUCKET` / `BACKUP_RCLONE_REMOTE`. |

> **Fail-open:** both replication paths log a warning on failure and
> return success from `backup.sh` — the local copy in `BACKUP_DIR` is
> authoritative. The restore drill in `.github/workflows/restore-drill.yml`
> exercises the local-copy path nightly; off-site integrity is the
> operator's responsibility (spot-check monthly).

### Tooling

| Variable | Type | Default | Notes |
|---|---|---|---|
| `FFMPEG_BIN` | str | `ffmpeg` | Resolved via `PATH` |
| `YTDLP_BIN` | str | `yt-dlp` | We use the Python lib; var is for diagnostics |
| `HTTPS_PROXY_URL` | str | empty | S9 audit fix: outbound proxy injected into `yt-dlp` opts for both `extract_info` and `download`. Empty = direct. Format `http://user:pass@host:port` or `socks5h://host:port`. Lets you egress through a regional proxy without leaking creds into process env. |

### Circuit breaker — yt-dlp upstream (`docs/34-error-taxonomy.md`, L5 audit fix)

The breaker is per-host and shared across the fleet via two Redis keys
(`cb:{host}:failures`, `cb:{host}:open_until`). When the breaker is
open, `YtDlpRunner.extract_info` / `download` raise
`UpstreamUnavailableError` (retryable) before calling upstream — the
job is rescheduled by arq and by the time the next attempt runs the
cooldown has typically expired.

| Variable | Type | Default | Notes |
|---|---|---|---|
| `CB_ENABLED` | bool | `true` | Master switch. Set `false` on single-process dev boxes without Redis. Production on both NL-1 (bot's analyze path) and NL-2 (worker's download path) should leave it `true`. |
| `CB_FAILURE_THRESHOLD` | int ≥1 | `5` | Open after this many consecutive "throttle-shaped" errors (HTTP 429 / "too many requests" / "rate limit"). A single success resets the counter. |
| `CB_WINDOW_SECONDS` | int ≥1 | `60` | TTL on the failure counter — `N` failures **within this window** trip the breaker. Longer windows forgive transient spikes; shorter windows trip faster. |
| `CB_COOLDOWN_SECONDS` | int ≥1 | `300` | How long the breaker stays open once tripped. Must cover a reasonable rate-limit backoff from the upstream (`300` is appropriate for YouTube/Instagram). |

### Error aggregation — Sentry / GlitchTip (L7 audit fix)

`app/observability/sentry.py::configure_sentry(settings, role=...)` is
called by every entrypoint (`main_bot`, `main_api`, `worker_settings._on_startup`,
`cleanup_worker`, `backup_worker`). It's a **no-op when `SENTRY_DSN=""`**,
and `sentry-sdk==2.19.2` is bundled in `requirements/prod.lock` so
flipping it on is a single env-var redeploy rather than an image
rebuild. The stdlib-logging integration captures `ERROR+` events
without us sprinkling `capture_exception` anywhere.

| Variable | Type | Default | Notes |
|---|---|---|---|
| `SENTRY_DSN` | str | empty | DSN from your Sentry / GlitchTip project. Empty disables the integration entirely. |
| `SENTRY_ENVIRONMENT` | str | empty | Falls back to `APP_ENV` when empty. Use e.g. `production`, `staging`. |
| `SENTRY_TRACES_SAMPLE_RATE` | float 0.0..1.0 | `0.0` | Performance tracing sample rate. `0.0` keeps Sentry quota to errors only. |
| `SENTRY_RELEASE` | str | empty | Release tag (typically the image sha). Leaving it empty means every event has no release correlation — prefer setting it during deploy, e.g. `SENTRY_RELEASE=$IMAGE_SHA`. |

Every event gets a `role` tag (`bot`, `api`, `worker`, `cleanup`, `backup`)
attached via `before_send` so incident queries can filter by the
originating process.

### Rate limiting (`docs/36-rate-limiting.md`)

| Variable | Type | Default | Notes |
|---|---|---|---|
| `RL_ENABLED` | bool | `false` | Master switch. `false` → bot uses `NoopRateLimitGate` (always allow); `true` → `RedisRateLimitGate`. Roll out per `docs/36-` §11. |
| `RL_USER_BURST` | str `"<n>/<sec>"` | `"5/60"` | L1 per-user burst. Parsed by `parse_rl_window`. |
| `RL_USER_HOURLY` | str | `"20/3600"` | L1 per-user hourly. |
| `RL_CHAT_BURST` | str | `"15/60"` | L2 per-chat burst (group fan-out cap). |
| `RL_GLOBAL_BURST` | str | `"60/60"` | L3 service-wide backstop. |
| `RL_DOMAIN_BURST` | str | `"30/60"` | L4 per upstream eTLD+1 (e.g. `youtube.com`). |
| `RL_NOTICE_TTL` | int 1..3600 | `30` | "Told already" cooldown — suppresses follow-up replies (`docs/36-` §4.3). |
| `RL_CHAT_NOTICE_TTL` | int 1..3600 | `30` | Same idea, scoped per chat (`docs/36-` §4.4). |
| `RL_FAIL_OPEN` | bool | `true` | If Redis is down, allow the request. Flipping to `false` in production is rejected by `validate_runtime` without an ADR (`docs/36-` §3.4). |

### Metrics / observability (`docs/35-metrics-and-slo.md` §7, `ADR-0006`, `ADR-0007`)

| Variable | Type | Default | Notes |
|---|---|---|---|
| `METRICS_ENABLED` | bool | `false` | Master switch. `false` → all three processes use `NoopRateLimitMetrics`/`NoopJobMetrics` and never open the metrics port. `true` → each process (bot, worker, API) starts its **own** in-process Prometheus exporter on `METRICS_PORT` (ADR-0007 §2.5 — one `/metrics` per process; the operator is expected to publish each on a distinct host port via `docker-compose`). |
| `METRICS_BIND_HOST` | str | `127.0.0.1` | Bind address for `/metrics`. `0.0.0.0` is rejected in production by `validate_runtime` (see `ADR-0006` §3.3 and `docs/35-` §7's "private port" rule). Applies to all three exporters. |
| `METRICS_PORT` | int 1..65535 | `9090` | Port for `/metrics` (intra-container). Publish through Docker only on the private NL-1 ↔ NL-2 network. Use distinct host ports per process — see `deploy/nl1/docker-compose.yml`, `deploy/nl2/docker-compose.yml`. |
| `METRICS_USER_ID_LABEL` | bool | `false` | If `true`, attaches `user_id` to `rate_limit_first_deny_total`. Acceptable at hundreds-of-users/day scale; set `false` before any public rollout (cardinality blow-up). The structured log event `rate_limit_first_deny` is emitted regardless, so LogQL `topk` keeps working. |
| `METRICS_QUEUE_SAMPLE_INTERVAL_S` | float 1.0..300.0 | `15.0` | How often the bot's `QueueDepthSampler` polls `ZCARD arq:dwtgbot` to refresh the `arq_queue_depth` gauge. Lower than 1 s wastes Redis CPU; higher than 60 s makes the B3 alert sluggish. ADR-0007 §2.6. |

---

## 3. Derived properties

Read these instead of recomputing in callers.

| Property | Returns | Notes |
|---|---|---|
| `is_production` | bool | `APP_ENV == production` |
| `telegram_max_upload_bytes` | int | `TELEGRAM_MAX_UPLOAD_MB * 1024 * 1024` |
| `max_file_size_bytes` | int | `MAX_FILE_SIZE_MB * 1024 * 1024` |
| `admin_ids` | `set[int]` | parsed from `BOT_ADMIN_IDS` |
| `database_url` | str | async DSN |
| `database_url_sync` | str | sync DSN for Alembic |
| `redis_url` | str | with optional `:password@` segment |

> **Rule:** if a value can be derived, expose it as a property — never let
> two pieces of code compute the same thing differently.

---

## 4. Validation

Two layers:

### Static (pydantic) — at construction time

- Type coercion (`int`, `Path`, etc.).
- Bounds: `Field(..., ge=..., le=...)`.
- Custom validators:
  - `LOG_LEVEL` is normalised to upper-case and matched against an
    allow-list.
  - `PUBLIC_BASE_URL` must start with `http://` or `https://`; trailing
    slash stripped.

A `pydantic.ValidationError` causes `get_settings()` to raise
`RuntimeError("Invalid configuration: ...")`. Entrypoints log and exit.

### Runtime — `Settings.validate_runtime(...)`

Called during startup of each process. Returns a list of human-readable
errors; **caller must abort** if non-empty.

```python
errors = settings.validate_runtime(require_storage=True, require_tools=True)
if errors:
    for e in errors: _logger.error("startup_check_failed", reason=e)
    raise RuntimeError("Startup checks failed: " + "; ".join(errors))
```

Checks performed:

| Check | When |
|---|---|
| `STORAGE_PATH` and `STORAGE_TMP_PATH` writable | when `require_storage=True` (worker, api, cleanup) |
| `FFMPEG_BIN` and `YTDLP_BIN` on PATH | when `require_tools=True` (worker only) |
| `API_INTERNAL_TOKEN` set | when `is_production` |
| `PUBLIC_BASE_URL` is HTTPS | when `is_production` |
| `INTERNAL_TEST_TOKEN` empty | when `is_production` (the dev-only test-enqueue endpoint must never ship to prod) |
| `RL_*` windows parse and `user_burst.limit ≤ chat_burst.limit ≤ global_burst.limit`, `user_burst.window_s ≤ user_hourly.window_s` | always (catches `docs/36-` §10 mistake #6 at startup) |
| `RL_FAIL_OPEN=true` | when `is_production` (flipping to fail-closed requires an ADR — `docs/36-` §3.4) |
| `METRICS_BIND_HOST` not in `{0.0.0.0, ::}` | when `METRICS_ENABLED` and `is_production` (public `/metrics` leaks operational signal — `ADR-0006` §3.3) |

The bot does not require storage or tools — it never writes to disk and
never invokes ffmpeg.

---

## 5. Per-process requirements

| Process | `require_storage` | `require_tools` | Other |
|---|---|---|---|
| `bot` | ❌ | ❌ | needs Telegram egress, DB, Redis |
| `api` | ✅ | ❌ | needs DB, storage volume (RO is fine) |
| `worker` | ✅ | ✅ | needs DB, Redis, storage RW, ffmpeg, Telegram egress |
| `cleanup` | ✅ | ❌ | needs DB, storage RW |
| `backup` | ❌ | ❌ | needs Postgres reachable; writes dumps to `BACKUP_DIR` |

If a worker starts on a host without ffmpeg, it dies during startup —
**by design**. The rule is "fail fast"; never start in a half-broken
state.

---

## 6. Where the env actually lives

```mermaid
flowchart LR
    subgraph Dev[Local dev]
        d1[.env at repo root] --> d2[get_settings reads via pydantic]
    end
    subgraph Prod_NL1[NL-1 prod]
        p1[deploy/nl1/.env] --> p2[docker compose env_file:]
        p2 --> p3[Container env]
        p3 --> p4[get_settings]
    end
    subgraph Prod_NL2[NL-2 prod]
        q1[deploy/nl2/.env] --> q2[docker compose env_file:]
        q2 --> q3[Container env]
        q3 --> q4[get_settings]
    end
```

- The repository ships `.env.example` (root) and per-stack examples under
  `deploy/nl1/.env.example`, `deploy/nl2/.env.example`. Real `.env` files
  are gitignored.
- Each stack has its **own** `.env` because NL-1 and NL-2 need different
  values for `POSTGRES_HOST`, `REDIS_HOST` (NL-2 connects to NL-1's
  private IP, not the docker service name).
- The interactive installer (`deploy/scripts/install.sh`) writes these
  env files for you — see [`20-deployment.md`](20-deployment.md).

---

## 7. Adding a new variable — checklist

- [ ] Add a typed field to `Settings` with `Field(...)` bounds when
      sensible.
- [ ] If derived, expose via `@property` (no caller-side recomputation).
- [ ] Update `.env.example` (root) **and** the per-stack examples
      (`deploy/nl1/.env.example`, `deploy/nl2/.env.example`).
- [ ] Document in this file under the right group.
- [ ] If it controls runtime behaviour, add a `validate_runtime` check
      (especially for "must be set in production").
- [ ] Add a unit test in `app/tests/test_config.py` (defaults + edge
      cases).
- [ ] If it changes the deploy story, update [`20-deployment.md`](20-deployment.md)
      and [`24-runbooks.md`](24-runbooks.md).

---

## 8. Anti-patterns

1. **Reading `os.environ` directly** anywhere outside `Settings`.
2. **Mutating `Settings` at runtime.** It's a frozen-by-convention DTO;
   restart the process to change config.
3. **Using `default=None` for required fields.** Use `Field(...)` so
   pydantic raises a clear "field required" message.
4. **Embedding secrets in `.env.example`.** Use placeholders
   (`changeme`, `<set-me>`) and warn loudly in comments.
5. **`POSTGRES_PASSWORD=postgres` in production.** Production must reject
   weak passwords — currently enforced operationally; future hardening:
   add a `validate_runtime` check.
6. **Setting `LOG_JSON=false` in production "to read logs more easily".**
   Production is JSON. If you need pretty logs, run `jq` locally.
7. **Bypass `get_settings()` by constructing `Settings()` directly.** You
   lose the cache and risk inconsistent reads.

---

## 9. Common mistakes

| Mistake | Symptom | Fix |
|---|---|---|
| Forgot to set `POSTGRES_PASSWORD` in NL-2's `.env` | Worker fails to connect | Set the same value as NL-1 |
| Set `PUBLIC_BASE_URL=http://...` in production | `validate_runtime` aborts startup | Change to `https://` and re-run |
| Bumped `WORKER_CONCURRENCY` without bumping `MAX_FILE_SIZE_MB` headroom | Disk fills | Reassess `MAX_FILE_SIZE_MB × WORKER_CONCURRENCY × N_workers` budget |
| Used `LOG_LEVEL=trace` | `ValueError: must be one of ...` | Use one of `DEBUG/INFO/WARNING/ERROR/CRITICAL` |
| `STORAGE_PATH` mounted from NFS | `EXDEV` errors, slow zips, intermittent failures | Use local block storage; NFS is unsupported |
| Production `.env` accidentally committed | Secret leak | Rotate the secret; force-push removal is not enough |
| Forgot `API_INTERNAL_TOKEN` in production | Startup fails | Generate a 32-byte random token, set in NL-1 and NL-2 envs |

---

## 10. Future extension points

- **Sealed secrets / Vault**: replace `.env` with a secrets manager;
  `Settings` could read from `/run/secrets/*` or call out at startup.
- **Hot reload**: today none. If we ever add it, restrict to a small set
  of "safe-to-change-online" fields (e.g. `LOG_LEVEL`).
- **`AppSettingModel` runtime overrides**: optional DB-backed feature
  flags (already a model exists; not yet wired). Would let SREs flip
  features without a deploy.
