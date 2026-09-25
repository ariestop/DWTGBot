# Changelog

All notable changes to this project are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Architectural rationale lives in `docs/adr/`. Each entry below links to
the relevant ADR when one applies.

---

## [Unreleased]

### Added — `cookies_setup.sh` and cookies step in the installer

`deploy/scripts/cookies_setup.sh instagram|youtube` installs a Netscape
cookies file from a path, from a terminal paste, or (Instagram) from
`sessionid` / `ds_user_id` / `csrftoken` typed in; validates it, writes
`/srv/dwtgbot/secrets/cookies-<provider>.txt` as `root:1000 0660`, sets
`*_COOKIES_FILE` in the host's `.env` files and recreates running
bot/worker. `install.sh` offers it on "Prepare server" when no Instagram
cookies exist and as menu item `[20]`. `deploy_update.sh` now also puts
`/srv/dwtgbot/secrets` in group 1000 — with `root:root 0750` the
containers (uid 1000) could not read the files. Docs no longer
recommend `root:root 0640` or `restart` after editing `.env`.

### Fixed — `lock-check` no longer fails on upstream releases

`make lock-check` resolved from an empty tmpdir, so any new release of a
transitive dependency turned CI red on an untouched tree and blocked the
image build and deploy that depend on it. `lock-check` and `make lock`
now seed the resolver with the committed `*.lock` (uv prefers existing
pins); `make lock-upgrade` is the explicit way to move everything to the
newest versions.

### Fixed — TLS bootstrap and renewal

`init-letsencrypt.sh` failed the first issue with `live directory exists`
because the self-signed dummy was never removed; it is now dropped right
before `certbot certonly` and restored if certbot fails. nginx reloads
every 6 h so certificates renewed by the `certbot` loop are actually
served.

### Changed — `install.sh` defaults to plain text menu (whiptail opt-in)

Operator feedback: the whiptail dialog hid the scrolling shell context
(last command output, healthcheck results, log tails) the moment it
drew, which is the wrong UX during a deploy where the operator is
reading state between actions. `bash deploy/scripts/install.sh` now
draws the numbered menu inline with the existing scrollback. Whiptail
remains available via `--whiptail` flag or `INSTALL_TUI_MODE=whiptail`
env var.

### Added — `install.sh` styled status panel + ASCII banner

The plain-mode menu now opens with an ASCII block-art banner and a
64-column unicode-bordered status panel showing what the operator
needs at a glance before picking an action:

* **Stack** — auto-detected from which `deploy/{nl1,nl2}/.env` exists
  (`nl1` / `nl2` / `both` / `none`).
* **Status pill** — ● RUNNING (green) when all compose services are up,
  ● DEGRADED (yellow) when partial, ● STOPPED (red) when none, ● n/a
  (yellow) when docker/compose isn't installed yet.
* **Compose** — `<up>/<total>` services, computed via
  `docker compose ps --services [--filter status=running]`.
* **Branch / HEAD** — `git rev-parse --abbrev-ref HEAD` + short SHA, so
  the operator sees which version they're about to act on.
* **Domain** — `SERVER_NAME` from the relevant `.env` (NL-2 host).
* **Docker** — server version (or `not installed`).
* **OS** — same `${OS_ID} ${OS_VER}` we already detect.

The menu items themselves are rendered as `[N]` with right-aligned
key cells so single- and double-digit options line up. UTF-8 padding
is locale-aware (`LC_ALL=C.UTF-8` is exported at the top of the
script) so multi-byte glyphs (`●`, `─`, `—`) don't push the right
border off by one column. The previous flat numbered list is gone;
operators who prefer the whiptail dialog still get it via
`--whiptail`.

### Fixed — Telegram preview crawler still burned `/d/<token>` slots despite `is_disabled=True`

The 2026-04-26 fix (`8bece85`) routed `disable_web_page_preview=True`
through PTB. PTB faithfully forwards
`link_preview_options.is_disabled=true` to `sendMessage`, but
production traffic on the same day proved this is **not enough**:
Telegram's preview crawler (`User-Agent: TelegramBot (like
TwitterBot)`) still issued real GETs against `/d/<token>` and
incremented `temp_links.downloads_count` by 1+ per message — exactly
the failure mode `is_disabled=true` is supposed to prevent. The flag
reliably hides the preview UI in clients but does not always suppress
the server-side fetch (likely cache warming / forward-rendering).

The structural fix removes the URL from the only surface Telegram's
crawler scrapes — the message text/caption — and puts it on a surface
it does not scrape: an inline `url=` button. URL buttons are
documented as not subject to preview generation, so the crawler
literally never sees the URL.

- `DeliveryService._deliver_via_link`: temp-link URL is delivered
  exclusively as an `InlineKeyboardButton(text="📥 Скачать", url=...)`
  prepended to the existing keyboard (preserves the "Получить текст
  поста" callback button when present). The message body now carries
  only filename + size + TTL hint; no URL, no `<a href="...">` anchor.
- `TelegramSender.send_text`: switched from the deprecated
  `disable_web_page_preview=` kwarg to the canonical
  `link_preview_options=LinkPreviewOptions(is_disabled=...)` (PTB
  21.x). Behaviour is identical (PTB's `parse_lpo_and_dwpp` does the
  same conversion) but explicit usage future-proofs us against the
  legacy alias being removed in PTB v22, and survives via
  defense-in-depth even if a future code change re-introduces a URL
  to the message body.
- Tests updated: `test_temp_link_delivery_attaches_button` now
  asserts the keyboard layout (download row + post-text row) and
  proves the URL is not in the message text. New regression
  `test_temp_link_delivery_no_post_text_still_has_download_button`
  guarantees we never fall back to a textual URL when the post-text
  store is empty.
- `docs/10-temp-links-and-delivery.md` §11: rewrote the "preview
  crawler" pitfall row with the new mitigation.

### Fixed — `/d/<token>` 502 Bad Gateway after every auto-deploy

`docker compose up -d api` rolls the api container, which gives it a new
IP in the `dwtgbot_media` bridge network. nginx (long-lived, **not**
recreated by the same `up -d`) had cached the old IP via
`upstream { server api:8080; }` — nginx resolves names inside `upstream`
exactly once at config-load time and caches forever. Result: every
`/d/<token>` request post-roll returned **502 Bad Gateway** with
`connect() failed (111: Connection refused) ... upstream:
"http://172.18.0.2:8080/..."` until somebody bounced nginx by hand.
Observed in production right after the temp-link 410 fix above shipped.

- `deploy/nginx/conf.d/media.conf.template`: dropped the named
  `upstream` block; `/d/` now sets `$api_upstream http://api:${API_PORT}`
  and uses it in `proxy_pass`. A variable in `proxy_pass` forces
  per-request re-resolution. Added `resolver 127.0.0.11 valid=10s
  ipv6=off;` (Docker's embedded DNS) so the new container's IP is
  picked up within ~10 seconds with no nginx reload.
- `deploy/scripts/deploy_update.sh`: added a defensive
  `restart_nginx_if_present` step on NL-2 — belt-and-suspenders for
  config edits to nginx mounts (which `up -d` would not otherwise
  apply) and for any future regression that reintroduces an upstream
  block.
- ADR-0004 §2 + `docs/10-temp-links-and-delivery.md` §5 updated with
  the rationale for not using a named upstream.

### Fixed — Temp-link 410 "Link expired or exhausted" on the first click

When the worker delivered a large file via a temp link, the message was
sent with Telegram's default **link preview enabled**. Telegram's
server-side preview crawler then fetched ``/d/<token>`` to render the
OG card, atomically incrementing ``temp_links.downloads_count`` (and on
some payloads making more than one Range probe). With
``TEMP_LINK_MAX_DOWNLOADS`` set low — or simply unlucky — the link was
already deactivated by the time the user tapped it, surfacing as
``{"detail": "Link expired or exhausted"}`` (HTTP 410) on what looked
like the very first click.

- ``DeliveryService._deliver_via_link`` now passes
  ``disable_web_page_preview=True`` when sending the temp-link
  message, so the crawler never reaches the protected endpoint and the
  user gets every slot of ``TEMP_LINK_MAX_DOWNLOADS``.
- ``TelegramSender.send_text`` grew a kwarg-only
  ``disable_web_page_preview`` parameter (default ``False``) — every
  other call site (failure notice, gallery caption) is unchanged.
- Regression covered by
  ``test_temp_link_delivery_attaches_button`` in
  ``app/tests/test_delivery_post_text_button.py``.
- Pitfall captured in ``docs/10-temp-links-and-delivery.md`` §11.

### Fixed — Bot silent after ``/start`` (webhook vs polling)

If a bot token previously had a **webhook** URL set, Telegram stops
delivering updates to **getUpdates** (polling) until the webhook is
deleted — the process looks healthy but never handles messages.
``post_init`` now calls ``delete_webhook`` before setting commands.

### Fixed — API crash: ``PrintLogger`` has no ``.name`` (structlog)

``configure_logging`` used ``PrintLoggerFactory`` together with
``structlog.stdlib.add_logger_name``, which reads ``logger.name``. Native
``PrintLogger`` has no ``name``, so ``main_api`` crashed on the first
``log.info`` with ``AttributeError`` (seen on Python 3.14 in Docker).

- Replaced with ``_add_logger_name_compat`` (handles ``PrintLogger``,
  ``None`` from the stdlib bridge, and ``_record`` when present).
- ``_StdlibFormatter`` now passes ``_record`` into the processor chain so
  third-party loggers resolve correctly.

### Fixed — NL-1 API crash loop (storage validation)

The internal API on NL-1 only serves ``/healthz``; it does not need
writable media dirs like the media-plane API on NL-2. ``main_api`` used
``validate_runtime(require_storage=True)`` while ``main_bot`` used
``require_storage=False``, so a control-plane host could pass the bot
checks but fail the API checks and restart forever.

- **`API_VALIDATE_STORAGE`** (default ``true``): NL-1 sets ``false`` in
  ``deploy/nl1/.env.example``; NL-2 keeps the default.
- **`deploy/scripts/healthcheck.sh`**: fixed the broken ``docker compose``
  probe (array expansion inside ``bash -c``); NL-1 postgres/redis/bot
  checks now use ``docker inspect`` health/status instead of fragile
  ``compose ps --status`` greps. Internal ``/healthz`` / ``/readyz`` probes
  no longer used ``bash -c 'compose_nl1 exec …'`` — **compose helpers are
  shell functions and are invisible inside a child ``bash -c``**, so the
  check always failed; replaced with ``docker exec`` + retry while
  uvicorn binds. NL-2 nginx probe also uses ``docker exec``. Follow-up:
  **45 s** retry for bot ``running`` + ``/healthz`` (slow cold start);
  ``API_PORT`` values are sanitised (quotes, ``#`` comments, CR).
- **`deploy/scripts/deploy_update.sh`**: skip pre-deploy backup when
  ``dwtgbot_postgres`` is not running (first boot).

### Fixed — GHCR image names must be lowercase

Docker rejects references like ``ghcr.io/owner/DWTGBot-bot`` with
``repository name must be lowercase``. CI now lowercases
``${{ github.repository }}`` before tagging/pushing, and the deploy
workflow lowercases ``GITHUB_REPOSITORY`` when exporting ``IMAGE_*``.
Examples in ``deploy/nl1/.env.example`` / ``deploy/nl2/.env.example`` use
``ghcr.io/ariestop/dwtgbot-*``.

### Docs — GHCR image paths in env examples

- **`deploy/nl1/.env.example`** / **`deploy/nl2/.env.example`**: default
  `IMAGE_*` lines use `ghcr.io/ariestop/dwtgbot-*` (lowercase; matches CI
  after the fix above) instead of `your-org/dwtgbot-*` placeholders.

### Docs — NL-1 Postgres/Redis (first-time operators)

- **`docs/20-deployment.md`** §5.1.1: explains that Postgres and Redis are
  **Docker services** on NL-1 (no `apt install`), what each env var
  means, the rule that `DATABASE_URL` must match `POSTGRES_PASSWORD`, and
  the `REDIS_URL` shape with/without a password. Includes a copy-paste
  `openssl` + `sed` recipe aligned with `deploy/nl1/.env.example`.
- The §5.1 shell snippet was corrected (`APP_ROLE=all` instead of an
  invalid `control-plane` value; `REDIS_URL` is no longer omitted when
  using the recipe from §5.1.1).

### Docs — Deploy readiness sweep

Closes the paper gap left by the S1-S10 + L-series code changes: the
new env vars existed in `.env.example` files but were absent from the
canonical catalogue and the deploy runbook, so operators couldn't
discover them during a fresh bring-up.

- **`docs/13-config-and-env.md`** §2: added reference rows for
  `DB_POOL_*` (S5), `STORAGE_MIN_FREE_MB` (S10), `XACCEL_ENABLED` (S6),
  `ORPHAN_JOB_AGE_SECONDS` (S1), `BACKUP_S3_BUCKET` / `BACKUP_S3_PREFIX`
  / `BACKUP_RCLONE_REMOTE` (S2), `HTTPS_PROXY_URL` (S9). New subsections
  for **Circuit breaker** (L5, four `CB_*` vars) and **Error
  aggregation — Sentry** (L7, four `SENTRY_*` vars). Each row explains
  the production-safe default and the "why" behind non-obvious values.
- **`docs/20-deployment.md`** §7.1: extended the per-host env matrix
  with the audit keys, calling out the asymmetric defaults (NL-2
  raises `DB_POOL_SIZE` to `20`; `XACCEL_ENABLED` stays `false` on
  NL-1; `STORAGE_MIN_FREE_MB` is NL-2-only). §6.4: added a blocker
  between NL-2 compose up and NL-1 having `migrate` done — NL-2 has
  no `migrate` service, and worker crash-loops without the schema.
  §10 checklists gained eight new items (S1/S2/S6/S10/L5/L7
  verifications). §11 common-mistakes table gained rows 19-23 for the
  new foot-guns (both off-site backends set; `XACCEL_ENABLED` on NL-1;
  orphan age too short; disk guard at `0`; `sentry-sdk` missing from
  a custom image build).
- **`docs/22-backup-restore.md`** §4.3: documented the built-in
  `replicate_offsite()` S3 + rclone paths (both binaries are now
  bundled in `docker/backup.Dockerfile`), including the creds-mount
  caveat — don't bake AWS keys / `rclone.conf` into the image.

### Fixed — Deploy-readiness gaps

- `deploy/nl1/.env.example` gained the four `CB_*` keys (L5),
  `HTTPS_PROXY_URL` (S9), `STORAGE_MIN_FREE_MB=0` (S10) and
  `XACCEL_ENABLED=false` (S6). The NL-1 operator no longer has to
  diff against the root `.env.example` to discover them.
- `docker/backup.Dockerfile` now installs Python deps from
  `requirements/prod.lock` with `--require-hashes` (L8 parity with
  api/bot/worker) and bundles `awscli` + `rclone` via apt so
  `deploy/scripts/backup.sh::replicate_offsite` (S2) has a real
  execution path in-container. The previous image ran unpinned
  `pip install pydantic pydantic-settings structlog` and the off-site
  step was a silent no-op that warned "aws-cli is missing" and
  returned success.
- `docker/api.Dockerfile` now `mkdir -p /var/lib/dwtgbot/{storage,tmp}
  && chown app:app` before `USER app`, mirroring `worker.Dockerfile`.
  On a fresh NL-2 compose up, api starts before worker (depends_on
  api-healthy); without the mkdir the named docker volume was seeded
  with root-owned dirs and the worker lost on its first
  `storage.init()`.

### Fixed — CI lockfile-check platform-independence

The April-2026 L8 commit shipped lockfiles generated on Windows,
which resolved to a different transitive graph than the one the
Ubuntu CI runner produced (`uvloop` only on Linux; `colorama` /
`tzdata` only on Windows). Three independent fixes:

- `make lock` now uses `uv pip compile --universal` — every
  platform-specific dep stays in the lock gated by an environment
  marker, and a single lockfile is correct everywhere.
- `make lock-check` runs the comparison compile inside a tmpdir with
  the same relative `-o requirements/$r.lock` path, so the
  uv-embedded header (which includes the output path by value)
  matches and the diff doesn't flap on byte-identical content.
- `.github/workflows/ci.yml::lockfile-check` pins `uv==0.11.7` — the
  generated header and resolution order are stable per uv version,
  so a floating install would flap the check on every minor release.

### Fixed — `LoggingIntegration` type mismatch

`configure_sentry` passed `event_level="ERROR"` to `LoggingIntegration`,
which sentry-sdk 2.x types as `int | None`. Not caught locally because
`sentry-sdk` was not installed in the dev venv (the import is behind a
`try/except ImportError`, so mypy couldn't see the real signature); CI
now installs from `requirements/prod.lock` where `sentry-sdk==2.19.2`
is pinned, and mypy rejects the str. Fixed by passing the stdlib int
`logging.ERROR`.

### Added — Audit roadmap L-series (medium-term improvements)

Second pass on the architecture audit: the "потом" tier from the
April-2026 roadmap. Scope: seven items (L3, L4, L5, L7, L8, L11,
L13) that improve scalability and DX without requiring an ADR or
a deploy-topology change. Items L1, L2, L6, L9, L10 are
intentionally deferred to dedicated sessions (each crosses an
architectural invariant); L12 fell out as dead after S3 removed
`AppSettingModel`.

- **L3 — Async-friendly Telegram upload.** `TelegramSender` now
  off-loads file reads through `asyncio.to_thread(path.read_bytes)`
  before handing the payload to `InputFile`. PTB's `InputFile`
  runs `.read()` synchronously in its constructor, so a 49 MiB
  upload was previously tens of milliseconds of CPU-blocking I/O
  on the event loop. Memory usage is bounded by
  `TELEGRAM_MAX_UPLOAD_MB × WORKER_CONCURRENCY` (≈ 200 MiB at the
  defaults).
- **L4 — Redis-backed notice throttle.** New
  `RedisNoticeThrottle` (`SET NX EX` per scope key) replaces the
  single-process `InMemoryNoticeThrottle` in `composition.build_bot`.
  Two bot replicas behind a shared webhook no longer both send
  "you're over the rate limit" to the same user. Fail-open on
  Redis errors mirrors `RedisRateLimitGate`.
- **L5 — Circuit breaker for upstream (yt-dlp).** Per-host
  breaker in Redis (`cb:{host}:failures` + `cb:{host}:open_until`)
  integrated into `YtDlpRunner`. When
  `CB_FAILURE_THRESHOLD` consecutive throttle-shaped errors
  (HTTP 429 / "rate limit" / "too many requests") land within
  `CB_WINDOW_SECONDS`, the breaker opens for
  `CB_COOLDOWN_SECONDS` — subsequent calls raise
  `UpstreamUnavailableError` (retryable) instead of hammering a
  throttled upstream and risking IP bans. New env vars
  `CB_ENABLED` (default `true`), `CB_FAILURE_THRESHOLD` (`5`),
  `CB_WINDOW_SECONDS` (`60`), `CB_COOLDOWN_SECONDS` (`300`).
- **L7 — Optional Sentry / GlitchTip integration.**
  `app/observability/sentry.py::configure_sentry(settings, role=...)`
  is called by every entrypoint (`main_bot`, `main_api`,
  `worker_settings._on_startup`, `cleanup_worker`,
  `backup_worker`). No-op when `SENTRY_DSN=""`; when set, wires
  the stdlib logging integration (`ERROR+` → event) with
  `send_default_pii=False` and tags every event with the
  originating process `role`. New env vars `SENTRY_DSN`,
  `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE` (default
  `0.0`), `SENTRY_RELEASE`. `sentry-sdk==2.19.2` added to
  `requirements/base.txt` so the code path is always available —
  flipping it on is a single env-var redeploy, not a dep bump.
- **L8 — Dependency lockfiles (`uv pip compile`).**
  `requirements/{base,dev,prod}.lock` now capture fully-resolved
  transitive pins with SHA-256 hashes. `Makefile` gains
  `make lock` (regenerate) and `make lock-check` (CI-friendly
  diff against a fresh compile). New CI job
  `lockfile-check` fails PRs that edit `requirements/*.txt`
  without regenerating the matching `.lock`. All prod
  Dockerfiles (`bot`, `api`, `worker`) install with
  `pip install --require-hashes -r requirements/prod.lock`, so
  image contents are bit-stable across builds.
  [`CONTRIBUTING.md`](CONTRIBUTING.md) documents the
  edit-lock-commit workflow.
- **L11 — Load-test scripts in `app/tests/load/`.**
  Lifted the shell recipes from `docs/37-load-and-capacity.md`
  §6 into executable scripts (`smoke.sh`, `concurrency.sh`,
  `mix.sh`) plus a Locust-based alternative (`locustfile.py`).
  Locust is intentionally **not** in `requirements/dev.txt` —
  the L11 tests are opt-in; `pip install locust` when you
  actually need open-model load with percentile reporting. All
  scripts use the existing `/internal/test/enqueue` endpoint
  (dev-only, gated three ways — see `docs/37` §6.1).
  [`app/tests/load/README.md`](app/tests/load/README.md)
  describes pre-requisites and the metric table to fill in per
  run.
- **L13 — Dedicated `cleanup_worker` composition.** New
  `CleanupComposition` bundle and `build_cleanup(settings)`
  builder. `cleanup_worker._amain` no longer reuses
  `build_api` — the cleanup process used to incidentally start
  a `/metrics` HTTP server (and an arq pool, and dev-only
  shims), which created port-collision risk when `api` and
  `cleanup` co-located on the same host. The new bundle carries
  only the four dependencies `_run_cycle` actually touches:
  `core`, `temp_links_repo`, `storage`, `media_cache_repo`,
  `jobs_repo`.

New tests: `test_redis_notice_throttle.py`,
`test_redis_circuit_breaker.py`, `test_sentry_config.py`.

`.env.example` + `deploy/{nl1,nl2}/.env.example` updated with
`CB_*` and `SENTRY_*` keys (safe defaults — circuit breaker on,
Sentry off).

### Added — Audit roadmap S1–S10 (technical-debt sweep)

Implements the "urgent" tier (S1–S10) of the April-2026
architecture audit. All items below are operationally
backwards-compatible: new env vars default to safe values
matching pre-audit behaviour, and the new schema in
`0002_drop_unused_tables.py` is reversible.

- **S1 — Orphan-job reaper.** New abstract method
  `JobsRepository.reap_orphan_processing(*, older_than_seconds)`
  marks `download_jobs` rows stuck in `PROCESSING` (worker crash,
  OOM-kill, stale advisory lock) as `FAILED` with reason
  `internal_error`. `cleanup_worker` calls it every cycle using
  the new `ORPHAN_JOB_AGE_SECONDS` env (default `7200` = 2 h).
- **S2 — Off-site backup replication.** `deploy/scripts/backup.sh`
  gained a `replicate_offsite` step that pushes the freshly
  rotated dump to S3 (`aws s3 cp`) or any rclone remote
  (`rclone copyto`) when `BACKUP_OFFSITE_KIND` ∈ `{s3,rclone,none}`
  is set. Default `none` preserves prior behaviour.
- **S3 — Drop unused tables.** Removed `AuditLogModel` and
  `AppSettingModel` from `app/infrastructure/db/models.py`
  (never written by any use case). Alembic revision
  `0002_drop_unused_tables` drops `audit_logs` / `app_settings`;
  `downgrade()` recreates the minimal schema for rollback.
- **S4 — Replace `pickle` in Redis state store with versioned JSON.**
  `RedisRequestStateStore` now serialises `AnalyzedMedia` payloads
  via `json` with a `v1:` schema prefix. Eliminates the
  arbitrary-code-execution risk on Redis tampering and removes the
  `S301` lint exemption. Old `pickle`-encoded keys are treated as
  cache misses (TTL-bounded; no migration required).
- **S5 — Externalise SQLAlchemy pool.** New env vars
  `DB_POOL_SIZE` (default `10`), `DB_MAX_OVERFLOW` (`5`),
  `DB_POOL_TIMEOUT_S` (`30`), `DB_POOL_RECYCLE_S` (`1800`) wired
  through `Settings` into `build_engine`. Defaults match the
  previous hard-coded values.
- **S6 — Harden `X-Internal-XAccel` trust.** API endpoint
  `/api/v1/dl/{token}` now serves `X-Accel-Redirect` only when
  **both** the loop-back marker header *and* the new
  `XACCEL_ENABLED=true` env are set. Nginx
  `media.conf.template` already overwrites any client-supplied
  marker (`proxy_set_header X-Internal-XAccel "1";`); the
  application-side gate prevents accidental exposure when the
  bot/API is reached directly (e.g. tests, mis-configured ingress).
- **S7 — Wire `MediaCacheRepository` into `AnalyzeLinkUseCase`.**
  Successful provider lookups are now upserted into
  `media_cache`; cache hits within
  `MEDIA_CACHE_FRESH_SECONDS` short-circuit the second
  `yt-dlp` extraction that previously ran during the
  "analyze → choose option → enqueue" flow. Existing
  `MEDIA_CACHE_MAX_AGE_SECONDS` continues to bound stale
  records; cleanup_worker still purges older rows.
- **S8 — Coverage in CI + nightly restore-drill.** `pytest`
  invocation in `.github/workflows/ci.yml` now collects
  coverage (`--cov=app --cov-report=xml`) and uploads
  `coverage.xml` as an artifact. New
  `.github/workflows/restore-drill.yml` workflow runs nightly
  (and on PRs touching `migrations/` or
  `deploy/scripts/`) — boots a throwaway Postgres, applies
  the full Alembic chain, and asserts that S3-dropped tables
  are gone while the active schema is intact.
- **S9 — Outbound HTTPS proxy for `yt-dlp`.** New
  `HTTPS_PROXY_URL` env (empty by default) is injected into
  `yt-dlp` opts in both `extract_info` and `download`, allowing
  egress through a regional proxy without leaking creds into
  process env.
- **S10 — Disk-space backpressure.** New `STORAGE_MIN_FREE_MB`
  env (default `1024` MiB). `LocalStorage.assert_free_space()`
  is invoked at the top of `ProcessDownloadUseCase._run()` (after
  the PROCESSING transition so the orphan reaper sees the row
  but before any allocation). Fail-permanent
  `StorageError` returns the user-friendly "сервис временно
  перегружен" message; the worker doesn't thrash a near-full
  disk with retries.

`.env.example` (root + `deploy/nl1` / `deploy/nl2`) updated with
all new keys and their safe defaults.

### Changed — Python runtime floor: 3.11 → 3.14 (ADR-0009)

The project's locked language floor moves from **CPython 3.11+** to
**CPython 3.14+** (supersedes [ADR-0005](docs/adr/0005-locked-architectural-assumptions.md)
row 1; full rationale in [ADR-0009](docs/adr/0009-python-314-runtime.md)).

- All build/runtime/CI surfaces pinned to **Python 3.14.4**:
  `pyproject.toml` (`requires-python = ">=3.14"`,
  `[tool.mypy] python_version = "3.14"`), `mypy.ini`,
  `.github/workflows/ci.yml` (lint / typecheck / tests jobs),
  `docker/{bot,api,worker,backup}.Dockerfile`
  (`ARG PYTHON_VERSION=3.14.4`, `python:3.14.4-slim`), `Makefile`
  (`PYTHON ?= python3.14`).
- Pinned compiled-deps bumped to the minimum versions that publish
  prebuilt cp314 wheels (no from-source builds in CI):
  `pydantic 2.9.2 → 2.13.2` (bundles `pydantic-core 2.46+`),
  `pydantic-settings 2.5.2 → 2.13.1`,
  `asyncpg 0.29.0 → 0.31.0`,
  `uvicorn[standard] 0.30.6 → 0.32.0` (uvloop ≥ 0.22 with cp314),
  `python-telegram-bot[ext] 21.6 → 21.11.1` (aiohttp with cp314),
  `yt-dlp 2024.10.7 → 2026.3.17` (older releases imported the
  removed-in-3.14 `imp` stdlib),
  `mypy 1.11.2 → 1.13.0` (parses 3.14 syntax — PEP 749 / t-strings).
- `[tool.ruff] target-version` deliberately stays on `"py313"` for now
  (see [ADR-0009 §3](docs/adr/0009-python-314-runtime.md)
  "Tooling caveat"): py314 target makes the formatter rewrite
  `except (A, B):` to PEP 758 unparenthesized form, which any tool
  running on a 3.13 host (mypy / pytest / IDE LSPs) cannot parse.
  Will flip once contributor toolchains are uniformly on 3.14.
- Pure-Python pins left unchanged on purpose (`SQLAlchemy`,
  `alembic`, `redis-py`, `arq`, `httpx`, `structlog`, `tenacity`,
  `prometheus-client`, `fastapi`) — bumping them would inflate the
  blast radius without addressing the runtime floor.
- 3.13 and earlier are dropped from the support matrix. Operators
  must rebuild images from the bumped Dockerfiles before pulling.
  Local devs need `pyenv install 3.14.4` (or equivalent) — the
  `requires-python` pin enforces this on `pip install`.
- Documentation aligned: `README.md`, `docs/00-overview.md`,
  `docs/02-architecture.md`, `docs/17-security.md`,
  `docs/18-testing-strategy.md`, `docs/19-docker-architecture.md`,
  `docs/24-runbooks.md`, `docs/25-agent-guide.md`,
  `docs/26-cursor-rules.md`, `docs/27-coding-standards.md`,
  `.cursor/rules/00-project-overview.mdc`,
  `.cursor/rules/60-docker-and-deploy.mdc`. ADR-0005 §3.1 retains
  the 3.11 history as a "superseded" note pointing at ADR-0009.
- Verified locally: `ruff check`, `ruff format --check`,
  `mypy app` (with `python_version = 3.14`), and `pytest -m "not
  integration"` (193 tests, 1 Postgres-dependent skip) all green
  against the bumped pin set.

### Fixed — audit Top-5 (ADR-0008)

The first project-wide audit (Architect / Backend / DevOps / SRE /
Security / QA hats) surfaced five top-priority issues. They are
shipped together because they share verification effort and rollback
strategy.

- **Retry semantics actually retry now.** `AppError` gained an
  `is_retryable` class flag; `ProviderError`, `DownloadError`,
  `DownloadTimeoutError` are retryable; `MediaPrivateError`,
  `MediaNotFoundError`, `FileTooLargeError`, `FfmpegError`,
  `StorageError`, config errors are not. The use case re-raises
  retryable / unexpected errors so arq schedules a real retry; the
  worker wrapper marks `FAILED` only on the terminal attempt
  (`mark_terminally_failed`). Previously, every error was caught and
  marked `FAILED` on the first try, making `JOB_MAX_RETRIES` a no-op.
  - Files: `app/exceptions.py`,
    `app/application/use_cases/process_download.py`,
    `app/infrastructure/queue/tasks.py`.
  - Tests: `app/tests/test_retry_semantics.py`.
  - Docs: `docs/16-error-handling.md` §1 + §2,
    `docs/09-queue-and-workers.md` §6 + §8.

- **Per-user concurrency cap is now atomic.** Added
  `JobsRepository.create_if_under_cap(job, *, cap)`; the SQLAlchemy
  implementation acquires `pg_advisory_xact_lock(user_id)` and runs
  the `count + insert` in one transaction. `EnqueueDownloadUseCase`
  now calls this single primitive instead of `count_active_for_user`
  followed by `create`. Concurrent enqueues for the same user can no
  longer both observe `active < cap` and both insert.
  - Files: `app/domain/repositories/jobs_repo.py`,
    `app/infrastructure/db/repositories/jobs_repo_impl.py`,
    `app/application/use_cases/enqueue_download.py`.
  - Tests: `app/tests/test_enqueue_cap_atomic.py`,
    updates to `app/tests/test_enqueue_use_case.py`,
    `app/tests/test_job_metrics.py`,
    integration test `app/tests/integration/test_advisory_lock_postgres.py`
    (opt-in, requires Postgres).
  - Docs: `docs/12-db-schema.md` §10.1.

- **Temp-link tokens redacted from nginx access logs.** New
  `map $request_uri $safe_request_uri` block rewrites
  `/d/<token>` to `/d/<redacted>` at log-emission time only;
  `log_format` switched from the catch-all `$request` to discrete
  fields using `$safe_request_uri`. Application functionality
  (`proxy_pass`, `X-Accel-Redirect`) sees the real URI; only the log
  line is sanitised.
  - Files: `deploy/nginx/nginx.conf`.
  - Docs: `docs/17-security.md` §2 + §9.

- **Image deployments are immutable by default.** Compose `image:`
  references switched to fail-fast `${IMAGE_*:?...}` form — a missing
  variable now fails `docker compose up` with a pointer to the
  variable rather than silently pulling `:latest`. The build workflow
  no longer attaches `:latest` to `main` builds (it is reserved for
  `vX.Y.Z` semver tags). The deploy workflow exports
  `IMAGE_*=ghcr.io/<repo>-<svc>:sha-<short>` derived from the chosen
  ref before invoking `deploy_update.sh`. A `concurrency:` group on
  `build-images.yml` prevents tag races for the same git ref.
  - Files: `deploy/nl{1,2}/docker-compose.yml`,
    `deploy/nl{1,2}/.env.example`,
    `.github/workflows/build-images.yml`,
    `.github/workflows/deploy.yml`.
  - Docs: `docs/21-cicd.md` §4.2.

- **Temp-link counter is now atomic.** Added
  `TempLinksRepository.try_register_use(token)`; the SQLAlchemy
  implementation runs a single `UPDATE temp_links SET
  downloads_count = downloads_count + 1, is_active = CASE WHEN ... END
  WHERE token = :t AND is_active AND expires_at > now AND
  downloads_count < max_downloads RETURNING *`. The `/d/{token}`
  handler runs cheap pre-checks (path traversal validation, file
  existence on disk) **before** the atomic call so probes / cleanup
  races never burn a download slot.
  - Files: `app/domain/repositories/temp_links_repo.py`,
    `app/infrastructure/db/repositories/temp_links_repo_impl.py`,
    `app/api/public/downloads.py`.
  - Tests: `app/tests/test_downloads_endpoint.py`,
    updates to `app/tests/test_temp_links.py`.
  - Docs: `docs/10-temp-links-and-delivery.md` §3 + §4.

### Documentation

- New ADR-0008 (`docs/adr/0008-audit-top5-fixes.md`) captures the
  context, the five decisions, alternatives considered, and the
  compliance hooks that prevent regressions.
- Updated `docs/16-error-handling.md`, `docs/09-queue-and-workers.md`,
  `docs/10-temp-links-and-delivery.md`, `docs/12-db-schema.md`,
  `docs/17-security.md`, `docs/21-cicd.md`, `docs/28-implementation-playbook.md`
  in lock-step with the code change. The playbook gained §2.3
  ("Contracts to preserve when touching hot paths") so future agents
  get a one-page anti-regression list.

### Migration / operator notes

- **Operators on existing hosts**: before the next deploy, set
  `IMAGE_BOT`, `IMAGE_API`, `IMAGE_WORKER`, `IMAGE_BACKUP` in
  `deploy/nl1/.env` and `deploy/nl2/.env`. `docker compose up` now
  refuses to start with an unset image var. The CI deploy workflow
  already exports them on its own; no action required for
  CI-driven deploys.
- **Log shippers**: anything that grepped nginx `access.log` for the
  full `/d/<token>` URI must re-point at the worker's
  `temp_link_served` event (which still carries a truncated `token`
  prefix). Full URIs are no longer recoverable from logs — by design.
- **No DB migration**: the new repository methods reuse existing
  columns. No `alembic upgrade head` required for these fixes.

### Tests

- Full suite: `python3 -m pytest app/tests` — 220 passing
  (one pre-existing baseline failure in `test_filenames.py` is
  unrelated and tracked separately).
- New opt-in integration test
  `app/tests/integration/test_advisory_lock_postgres.py` exercises
  the real `pg_advisory_xact_lock` against a Postgres instance
  (skipped when `DWTGBOT_TEST_POSTGRES_URL` is not set).
