# DWTGBot — Telegram Media Downloader

Production-ready Telegram bot that downloads media from **YouTube** and **Instagram**,
delivers small files directly via Telegram and large files through **tokenized
temporary HTTPS links** served by Nginx.

Built for a **two-server topology**:

- **NL-1 (control plane)** — bot, Postgres, Redis, backups
- **NL-2 (media plane)** — worker (yt-dlp + ffmpeg), Nginx + Certbot, file storage

The two halves talk to each other over a **private network** (e.g. WireGuard).
Only `:80`/`:443` are exposed to the internet on NL-2.

---

## Table of contents

- [Features](#features)
- [Architecture](#architecture)
- [Quickstart (single host, dev)](#quickstart-single-host-dev)
- [Production deploy](#production-deploy) — see also [`docs/20-deployment.md`](docs/20-deployment.md)
- [Configuration reference](#configuration-reference)
- [Operations](#operations)
- [Development](#development)
- [Testing & CI](#testing--ci)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting) — see also [`docs/24-runbooks.md`](docs/24-runbooks.md) (operational runbooks) and [`docs/31-troubleshooting.md`](docs/31-troubleshooting.md) (diagnostics)
- [License](#license)

---

## Features

- **Commands**: `/start`, `/help`, `/health`, `/about`.
- **YouTube**: real available qualities (360p / 480p / 720p / 1080p) + MP3 audio,
  estimated file sizes, video+audio merging via ffmpeg.
- **Instagram**: single video/photo, carousel/gallery (download all, video-only, photo-only),
  optional ZIP packaging.
- **Telegram 50 MB limit aware** — anything over the configured limit is delivered
  as a tokenized temp link (TTL + max downloads, served via Nginx `X-Accel-Redirect`).
- **Async background processing** — `arq` queue on Redis, idempotent jobs, retries
  with backoff, dead-letter logging, user-friendly status messages.
- **Production logging** — `structlog` JSON output with `request_id` / `job_id` /
  `user_id` / `chat_id` correlation.
- **Health endpoints** — `/healthz` (liveness) and `/readyz` (DB + Redis + storage).
- **Operational tooling** — interactive `install.sh` menu, idempotent
  `deploy_update.sh`, automated Postgres backups + restore, expired-link cleanup,
  ufw firewall presets.
- **Containerized** — multi-stage Dockerfiles, non-root users, two `docker-compose`
  stacks, Certbot SSL bootstrap.
- **CI/CD** — GitHub Actions: ruff + mypy + pytest + shellcheck, GHCR image
  publishing, manual SSH deploy.

---

## Architecture

```
                          ┌──────────────┐
                          │  Telegram    │
                          │  Bot API     │
                          └──────┬───────┘
                                 │ long-poll
                                 ▼
   ┌─────────────────────────────────────────────────────┐
   │ NL-1 (control plane)                                │
   │                                                     │
   │   bot ──enqueue──► redis ◄──poll── worker (NL-2)    │
   │     │                                               │
   │     └─► postgres (jobs, temp_links, media_cache)    │
   │                                                     │
   │   api (internal /healthz /readyz)                   │
   │   backup (cron pg_dump → /var/backups)              │
   └────────────────────────┬────────────────────────────┘
                            │ private network (WireGuard)
                            │   • Postgres :5432
                            │   • Redis    :6379
                            ▼
   ┌─────────────────────────────────────────────────────┐
   │ NL-2 (media plane)              Internet ► :80/:443 │
   │                                          │          │
   │   nginx ──► api (public /d/{token})      │          │
   │     │                                    │          │
   │     │   X-Accel-Redirect ◄──────────────┘          │
   │     ▼                                               │
   │   /_protected/  (internal)  ──► STORAGE_PATH        │
   │                                                     │
   │   worker  (yt-dlp + ffmpeg)  ──► STORAGE_PATH       │
   │     │                                               │
   │     └─► sends small files directly via Telegram Bot │
   │                                                     │
   │   cleanup (deactivate expired links + purge files)  │
   │   certbot (auto-renew Let's Encrypt)                │
   └─────────────────────────────────────────────────────┘
```

Layers (`app/`):

| Layer | Folder | Purpose |
|---|---|---|
| Domain | `domain/` | Pure entities, enums, repository interfaces |
| Application | `application/` | Use cases + service protocols + DTOs |
| Infrastructure | `infrastructure/` | DB, Redis, queue, providers, downloader, storage, telegram |
| Bot | `bot/` | python-telegram-bot handlers, callbacks, keyboards, middleware |
| API | `api/` | FastAPI internal health + public temp-link routes |
| Workers | `workers/` | Long-running cleanup + backup processes |
| Composition | `composition.py` | Dependency wiring (composition root) |

---

## Quickstart (single host, dev)

Requirements: Python 3.11+, Docker, ffmpeg. Production target OS: **Ubuntu 24.04 LTS**.

```bash
git clone <repo-url> dwtgbot && cd dwtgbot
cp .env.example .env                 # fill in BOT_TOKEN at minimum
make dev-install                     # creates .venv + installs dev deps
make precommit-install               # optional, recommended

# Bring up Postgres + Redis only via the NL-1 stack:
cp deploy/nl1/.env.example deploy/nl1/.env
deploy/scripts/install.sh            # interactive menu (or use Makefile targets)

# Run bot/api/worker in three terminals:
make migrate
make bot
make api
make worker
```

Send a YouTube/Instagram link to your bot in Telegram. You should see download
options as inline buttons.

---

## Production deploy

The full step-by-step is in [`docs/20-deployment.md`](docs/20-deployment.md)
(canonical) and [`docs/24-runbooks.md`](docs/24-runbooks.md) for incident
response. Short version:

1. **Provision two VMs** (NL-1 control plane, NL-2 media plane). **Ubuntu 24.04
   LTS only** — the installer pins the Docker apt repo to `noble`. Set up a
   private network between them (WireGuard recommended) so NL-2 can reach
   NL-1 on `:5432` and `:6379`.
2. **Clone the repo** to `/opt/dwtgbot` on both servers.
3. **Run the installer** on each:
   ```bash
   cd /opt/dwtgbot
   sudo bash deploy/scripts/install.sh
   ```
   Use the menu to: install Docker → write `deploy/nl1/.env`
   (or `deploy/nl2/.env`) → configure ufw → bring the stack up.
4. **NL-2 only**: obtain SSL certificates:
   ```bash
   sudo bash deploy/scripts/certbot_init.sh
   ```
5. **Verify** via `bash deploy/scripts/healthcheck.sh nl1` (and `nl2`).

### Auto-deploy via GitHub Actions

Configure these secrets per environment (`nl1` and `nl2`):

| Secret | Description |
|---|---|
| `NL1_HOST` / `NL2_HOST` | Public IP / DNS |
| `NL1_SSH_USER` / `NL2_SSH_USER` | SSH user with `sudo` |
| `NL1_SSH_KEY` / `NL2_SSH_KEY` | Private key (PEM) |
| `NL1_SSH_PORT` / `NL2_SSH_PORT` | Optional, default 22 |
| `NL1_REPO_PATH` / `NL2_REPO_PATH` | Where the repo is cloned (e.g. `/opt/dwtgbot`) |

Then trigger **Actions → Deploy → Run workflow** with `target=both` and the desired
ref. The workflow runs `deploy/scripts/deploy_update.sh` over SSH on each host.

---

## Configuration reference

All settings come from environment variables, validated by `pydantic-settings`
on startup. Process aborts immediately if anything is invalid or production-only
constraints are violated.

| Var | Default | Notes |
|---|---|---|
| `APP_ROLE` | `all` | `bot` \| `api` \| `worker` \| `all` (dev only) |
| `APP_ENV` | `development` | `production` enforces HTTPS + internal token |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `LOG_JSON` | `false` | Use `true` in production for structured logs |
| `BOT_TOKEN` | — | **Required**, from @BotFather |
| `BOT_ADMIN_IDS` | empty | Comma-separated TG user IDs |
| `TELEGRAM_MAX_UPLOAD_MB` | `49` | Files larger than this go through temp links |
| `POSTGRES_*` | — | Overridden by `DATABASE_URL` if set |
| `DATABASE_URL` | derived | `postgresql+asyncpg://…` |
| `REDIS_*` | — | Overridden by `REDIS_URL` if set |
| `STORAGE_PATH` | `/var/lib/dwtgbot/storage` | Final artefacts |
| `STORAGE_TMP_PATH` | `/var/lib/dwtgbot/tmp` | Scratch workdirs |
| `MAX_FILE_SIZE_MB` | `2048` | Hard upper bound on a single download |
| `PUBLIC_BASE_URL` | `http://localhost:8080` | **Must be HTTPS in prod** |
| `TEMP_LINK_TTL_SECONDS` | `86400` | Link lifetime |
| `TEMP_LINK_MAX_DOWNLOADS` | `5` | Use limit per token |
| `TEMP_LINK_TOKEN_BYTES` | `32` | URL-safe entropy |
| `API_INTERNAL_TOKEN` | — | **Required in prod** |
| `WORKER_CONCURRENCY` | `2` | Parallel downloads per worker |
| `JOB_TIMEOUT_SECONDS` | `1800` | arq job hard timeout |
| `JOB_MAX_RETRIES` | `2` | arq retry budget |
| `DOWNLOAD_TIMEOUT_SECONDS` | `900` | yt-dlp hard timeout |
| `CLEANUP_INTERVAL_SECONDS` | `3600` | Cleanup loop tick |
| `MEDIA_CACHE_TTL_SECONDS` | `21600` | media_cache row freshness |
| `BACKUP_DIR` | `/var/backups/dwtgbot` | Where pg_dump writes |
| `BACKUP_RETENTION_DAYS` | `14` | Older dumps are pruned |
| `FFMPEG_BIN` / `YTDLP_BIN` | `ffmpeg` / `yt-dlp` | Override for non-standard paths |

See [`.env.example`](.env.example), [`deploy/nl1/.env.example`](deploy/nl1/.env.example),
[`deploy/nl2/.env.example`](deploy/nl2/.env.example) for full templates.

---

## Operations

All ops live under `deploy/scripts/`. They share `helpers.sh` (strict mode,
colored logs in `/var/log/dwtgbot.log`, ERR-trap, interactive confirms,
`compose_nl1`/`compose_nl2` wrappers).

| Script | Purpose |
|---|---|
| `install.sh` | Interactive whiptail menu (Docker install, env setup, firewall, start/stop/logs, backup/restore, deploy, certbot, cleanup, healthcheck) |
| `deploy_update.sh nl1\|nl2` | git pull → config check → optional pre-backup → pull → up -d → migrate (NL-1) → healthcheck |
| `backup.sh` | `pg_dump` (in-container or via host exec), gzip, retention prune |
| `restore.sh` | Interactive picker, drops/recreates DB, reloads dump, restarts dependents |
| `firewall_setup.sh nl1\|nl2` | `ufw` rules; NL-1 requires `PRIVATE_NET` for Postgres/Redis |
| `cleanup.sh` | Removes old `STORAGE_TMP_PATH/*`, runs one-shot cleanup_worker pass |
| `certbot_init.sh` | Bootstraps Let's Encrypt cert on NL-2 |
| `healthcheck.sh nl1\|nl2` | Per-service status + HTTP probes |

Set `ASSUME_YES=1` to bypass interactive prompts in automation.

### Metrics & SLOs

When `METRICS_ENABLED=true`, each of the three processes (bot, worker,
API) exposes its own in-process Prometheus exporter on
`http://${METRICS_BIND_HOST}:${METRICS_PORT}/metrics`. Bind to a
private interface only — `validate_runtime` blocks `0.0.0.0` in
production. See [`docs/35-metrics-and-slo.md`](docs/35-metrics-and-slo.md)
for the SLO catalogue and the live metric inventory; the architectural
shape of the three-process exporter is in
[`docs/adr/0007-job-queue-worker-metrics.md`](docs/adr/0007-job-queue-worker-metrics.md).

---

## Development

```bash
make dev-install      # .venv + dev deps
make fmt              # ruff format (black-compatible) + ruff --fix
make lint             # ruff check
make typecheck        # mypy
make test             # pytest
make precommit-install
```

> Formatter is **`ruff format`** (a byte-compatible reimplementation of `black`,
> ~30× faster). We do not use `black` directly to avoid two formatters in the
> same repo.

Run individual processes locally (Postgres + Redis from the NL-1 compose stack):

```bash
make migrate
make bot       # python -m app.main_bot
make api       # python -m app.main_api
make worker    # python -m app.main_worker
```

---

## Testing & CI

- Unit tests live in `app/tests/`. They use fakes for repos/queues; no real
  network/DB/Redis is touched.
- Integration tests should be marked with `@pytest.mark.integration`. They
  are excluded by default (`pytest -m "not integration"`).
- `.github/workflows/ci.yml` runs ruff format + check, mypy, pytest, and
  shellcheck on every PR.
- `.github/workflows/build-images.yml` builds and pushes `bot`/`api`/`worker`/`backup`
  images to GHCR on `main` and tags.
- `.github/workflows/deploy.yml` is manual; SSHes to NL-1/NL-2 and runs
  `deploy_update.sh`.

---

## Project layout

```
app/
  bot/                   python-telegram-bot handlers, keyboards, callbacks
  api/                   FastAPI: internal health + public /d/{token}
  application/           Use cases, service protocols, DTOs
  domain/                Pure entities, enums, repository interfaces
  infrastructure/        DB, cache, queue, providers, downloader, storage, telegram
  workers/               cleanup_worker, backup_worker
  composition.py         Composition root (DI wiring)
  config.py              pydantic-settings
  logging_config.py      structlog setup
  exceptions.py          AppError hierarchy
  main_bot.py            Bot entrypoint
  main_api.py            API entrypoint
  main_worker.py         arq worker entrypoint
  tests/                 Unit tests
deploy/
  nl1/docker-compose.yml      Control plane
  nl2/docker-compose.yml      Media plane
  nginx/                      Nginx config + snippets + media.conf.template
  certbot/init-letsencrypt.sh
  scripts/                    Operational bash scripts
  templates/env.template      Generic env template used by installer
docker/
  bot.Dockerfile  api.Dockerfile  worker.Dockerfile  backup.Dockerfile
docs/
  DEPLOY.md  ARCHITECTURE.md  TROUBLESHOOTING.md
migrations/
  env.py  script.py.mako  versions/
.github/workflows/
  ci.yml  build-images.yml  deploy.yml
```

---

## Troubleshooting

Operational runbooks for the 20 most common incidents are in
[`docs/24-runbooks.md`](docs/24-runbooks.md); deep-dive diagnostics
(log analysis, error classification, symptom→cause tables) live in
[`docs/31-troubleshooting.md`](docs/31-troubleshooting.md).
A few quick pointers:

- **"Sorry, I can't process this link"** → check worker logs:
  `compose_nl2 logs -f worker`. Usually yt-dlp metadata fetch failed
  (private/region-locked content).
- **Files never delivered, jobs stuck `pending`** → Redis connectivity from NL-2.
  Verify `REDIS_HOST`/`REDIS_URL` and the private network.
- **Temp link returns 404** → either the file was already cleaned up, the link
  expired, or `STORAGE_PATH` does not match between worker and Nginx volumes.
- **Certbot fails on first run** → ensure DNS A-record points to NL-2 and `:80`
  is open in ufw and at the cloud provider level.

---

## Architectural decisions (locked)

The following choices are intentional and fixed. Don't change them in PRs
without an explicit RFC discussion.

| # | Area | Decision |
|---|---|---|
| 1 | Language | Python 3.11+ |
| 2 | Bot framework | python-telegram-bot |
| 3 | Queue | Redis (arq) |
| 4 | Database | PostgreSQL + SQLAlchemy 2.x + Alembic |
| 5 | Download engine | yt-dlp |
| 6 | Media processing | ffmpeg |
| 7 | Delivery | small files → Telegram; big files → temporary HTTPS link |
| 8 | Infrastructure | NL-1: bot + redis + postgres; NL-2: worker + nginx + certbot + cleanup |
| 9 | Deployment | Docker Compose |
| 10 | CI/CD | GitHub Actions |
| 11 | OS target | Ubuntu 24.04 LTS |
| 12 | Interactive installer | bash (`deploy/scripts/install.sh`) |
| 13 | Logging | JSON structured logs (`structlog`) |
| 14 | Lint / format / types | `ruff check` + `ruff format` (black-compatible) + `mypy` |
| 15 | Tests | `pytest` |

## License

MIT — see [`LICENSE`](LICENSE).
