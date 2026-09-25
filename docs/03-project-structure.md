# 03 — Project structure & dependency rules

> Status: Stable
> Audience: engineers, AI agents (mandatory before any change)
> Read next: [`04-domain-model.md`](04-domain-model.md), [`27-coding-standards.md`](27-coding-standards.md)

This document is the canonical map of the repository. Every folder is
listed with its purpose, what belongs in it, what does **not** belong in it,
and which other folders it may import from.

The dependency rules below are enforced by review. Violations should be
caught in CI by `ruff check` (no rule today validates layering automatically;
it is on the human/AI reviewer to enforce).

---

## 1. Top-level layout

```
.
├── app/                  # Application source (Python package)
│   ├── api/              # FastAPI routes (internal + public)
│   ├── application/      # Use cases + service protocols + DTOs
│   ├── bot/              # python-telegram-bot adapter (handlers, callbacks)
│   ├── domain/           # Pure entities, enums, repository ABCs
│   ├── infrastructure/   # Concrete adapters (DB, Redis, queue, providers, …)
│   ├── tools/            # Optional CLI tools (e.g. requeue.py)
│   ├── utils/            # Cross-cutting utilities (url, filenames, retries, …)
│   ├── workers/          # Long-running loops (cleanup, backup)
│   ├── tests/            # Unit tests
│   ├── composition.py    # Composition root
│   ├── config.py         # pydantic-settings
│   ├── exceptions.py     # AppError hierarchy
│   ├── logging_config.py # structlog setup
│   ├── main_bot.py       # Bot entrypoint
│   ├── main_api.py       # API entrypoint
│   └── main_worker.py    # Worker entrypoint
│
├── deploy/
│   ├── compose/          # Shared fragments: control.yml, media.yml (ADR-0011)
│   ├── single/           # Single-host stack (both fragments) + .env.example
│   ├── nl1/              # NL-1 docker-compose stack + .env.example
│   ├── nl2/              # NL-2 docker-compose stack + .env.example
│   ├── nginx/            # nginx.conf + snippets + media.conf.template
│   ├── certbot/          # init-letsencrypt.sh
│   ├── scripts/          # Operational bash scripts
│   └── templates/        # env.template (used by installer)
│
├── docker/
│   ├── bot.Dockerfile
│   ├── api.Dockerfile
│   ├── worker.Dockerfile
│   └── backup.Dockerfile
│
├── docs/                 # All documentation (this folder)
├── migrations/           # Alembic
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── requirements/         # Pinned dependency files
│   ├── base.txt
│   ├── dev.txt
│   └── prod.txt
├── .github/workflows/    # GitHub Actions
│
├── .env.example          # Local-dev env template
├── .pre-commit-config.yaml
├── alembic.ini
├── CONTRIBUTING.md
├── LICENSE
├── Makefile
├── pyproject.toml
└── README.md
```

---

## 2. `app/` — folder by folder

### 2.1 `app/domain/`

**Purpose:** the **innermost** layer. Pure Python, no framework, no I/O.
Contains the language used by the rest of the application to describe the
problem.

**Subfolders:**
```
app/domain/
├── entities/            # Dataclasses representing core concepts
│   ├── download_job.py  # DownloadJob (incl. state transitions)
│   ├── media_info.py    # MediaInfo, DownloadOption, MediaItem, DownloadResult
│   └── temp_link.py     # TempLink (incl. is_usable, register_use)
├── enums.py             # Platform, JobStatus, MediaKind, DeliveryMethod
└── repositories/        # Abstract repository interfaces (ABCs)
    ├── jobs_repo.py
    ├── media_cache_repo.py
    └── temp_links_repo.py
```

**You may put here:**
- Frozen / mutable dataclasses with business invariants in methods.
- Enums.
- Pure functions on entities.
- Abstract base classes for repositories.

**You may NOT put here:**
- SQLAlchemy / asyncpg / Redis / arq / yt-dlp / telegram imports.
- File I/O, network I/O, subprocess.
- Logging configuration (logger usage is OK; configuration is not).
- Anything that requires a runtime config value.

**May import from:** standard library only (plus `app.utils` for *pure*
helpers like `enums`, but not utilities that touch I/O).

### 2.2 `app/application/`

**Purpose:** orchestrates domain entities to fulfil use cases. Depends on
abstractions, not on concrete adapters.

**Subfolders:**
```
app/application/
├── dto/                 # Data Transfer Objects (frozen dataclasses)
│   ├── jobs.py          # EnqueueDownloadInput / Result, WorkerJobPayload
│   └── media.py         # AnalyzedMedia
├── ports/               # Порты к адаптерам (Protocol), ADR-0012
│   ├── media_sender.py  # MediaSender + DTO InlineKeyboard / InlineButton
│   ├── media_storage.py # MediaStorage
│   ├── progress_channel.py   # Redis-ключи side-channel прогресса (контракт worker ↔ bot)
│   └── progress_reporter.py  # ProgressReporter + NoopProgressReporter
├── services/            # Service protocols (Protocol/ABC)
│   ├── delivery_service.py
│   ├── providers.py     # Provider, ProviderRegistry
│   ├── queue.py         # QueueProducer
│   ├── request_state_store.py
│   └── temp_link_service.py
└── use_cases/
    ├── analyze_link.py        # User sent URL → present options
    ├── enqueue_download.py    # User picked option → push to queue
    └── process_download.py    # Worker: do the actual download + delivery
```

**You may put here:**
- Use cases that orchestrate repositories + services.
- Protocols / ABCs that infrastructure will implement.
- DTOs to pass data between layers.

**You may NOT put here:**
- Any concrete infrastructure (no SQLAlchemy session, no `redis.Redis()`).
- Web/Telegram framework code.
- Subprocess calls.

**May import from:** `app.domain.*`, `app.utils.*`, standard library, typing.
Сторонние SDK (`telegram`, `sqlalchemy`, `redis`, `yt_dlp` …) и
`app.infrastructure.*` запрещены; правило для `app/domain` и
`app/application` проверяет `app/tests/test_layering.py`.

### 2.3 `app/infrastructure/`

**Purpose:** concrete implementations of domain repositories and application
service protocols. Everything that talks to the outside world.

**Subfolders:**
```
app/infrastructure/
├── cache/                # Redis client + RedisRequestStateStore
├── db/                   # SQLAlchemy: base, session, models, repositories impl
│   ├── repositories/
│   ├── base.py
│   ├── models.py
│   └── session.py
├── downloader/           # YtDlpRunner + ytdlp_opts / ytdlp_results, mobile_compat (ffprobe/ffmpeg post-step)
├── providers/            # BaseProvider, YouTubeProvider, InstagramProvider, registry
├── queue/                # arq pool, producer, tasks, worker_settings
├── storage/              # LocalStorage (filesystem owner) — implements MediaStorage
└── telegram/             # TelegramSender (worker-side telegram.Bot) — implements MediaSender
```

**You may put here:**
- Driver/library glue (SQLAlchemy ORM, Redis client, arq settings, yt-dlp wrappers).
- Implementations of `app.domain.repositories.*` and `app.application.services.*`.
- Subprocess wrappers (with safe argv handling — see [`08`](08-download-pipeline.md)).

**You may NOT put here:**
- Use cases (those live in `app.application`).
- Telegram bot handlers (those live in `app.bot`).
- Direct calls to other infrastructure modules across boundaries beyond what
  the protocol allows (e.g. `db/repositories` should not call into `cache/`).

**May import from:** `app.domain.*`, `app.application.*` (only protocols/DTOs),
`app.utils.*`, third-party libraries.

### 2.4 `app/bot/`

**Purpose:** the python-telegram-bot adapter. Translates Telegram updates
into use case calls and renders responses.

```
app/bot/
├── application.py        # Application factory (build_application)
├── container.py          # BotContainer dataclass (DI)
├── callbacks/
│   ├── cancel_job.py     # Кнопка отмены (ADR-0010 §2.1)
│   ├── codec.py          # Callback wire formats (encode/decode + 64-byte limit)
│   ├── download.py       # Inline-button callback handler
│   └── post_text.py      # Кнопка «текст поста» (ADR-0010 §2.3)
├── handlers/
│   ├── commands.py       # /start /help /about /health
│   ├── errors.py         # Global error handler → user-friendly message
│   └── links.py          # Free-text URL handler
├── keyboards/
│   └── download_options.py
├── middleware/
│   ├── logging_mw.py     # Binds correlation IDs into structlog context
│   └── metrics_mw.py     # bot_message_handled latency / outcome (ADR-0007)
└── services/
    ├── progress_caption.py  # Чистый текст подписи прогресса и полоска
    └── progress_updater.py  # pubsub + watchdog: правка placeholder-сообщения
```

**You may put here:**
- Telegram-specific code: handlers, parsers, keyboards.
- Adaptation of `AppError` to user-visible messages.
- Update logging / context binding.

**You may NOT put here:**
- Any business logic (ask "could this be reused without Telegram?" — if yes,
  it belongs in `app.application` or `app.domain`).
- Direct DB / Redis / Provider calls.
- Long-running loops (those live in `app.workers`).

**May import from:** `app.application.*` (use cases + services as protocols),
`app.domain.*` (enums, DTOs), `app.utils.*`. **Must not** import
`app.infrastructure.*`.

### 2.5 `app/api/`

**Purpose:** FastAPI app for internal healthchecks and the public temp-link
endpoint.

```
app/api/
├── app.py                # create_app + lifespan
├── internal/
│   └── health.py         # /healthz, /readyz
└── public/
    └── downloads.py      # /d/{token}
```

**Same rules as `app/bot/`** — adapter only, no business logic. May import
from `app.application.*` (especially `TempLinkService`) and from
`app.composition` for lifespan wiring.

### 2.6 `app/workers/`

**Purpose:** standalone long-running loops outside the bot/api/arq-worker
processes.

```
app/workers/
├── backup_worker.py      # Periodically calls deploy/scripts/backup.sh
└── cleanup_worker.py     # Periodically deactivates expired temp links + GC files
```

**Rules:** like `app/bot/` and `app/api/` — they invoke use cases / services
through the composition root.

### 2.7 `app/utils/`

**Purpose:** small, **pure** cross-cutting helpers that any layer can import.

```
app/utils/
├── correlation.py        # request_id generation, structlog binding
├── filenames.py          # sanitize_filename, ensure_within
├── retries.py            # tenacity policies
└── url.py                # extract_first_url, detect_platform, detect
```

**You may put here:**
- Helpers that have **no I/O** and **no framework** dependencies.
- Things that would otherwise be duplicated across layers.

**You may NOT put here:**
- Anything that needs `Settings` to function (push it into a service or pass
  config in as an argument).
- Subprocess wrappers (use `app.infrastructure.downloader.*` instead).
- Code that imports from anywhere in `app/` other than `app/exceptions.py`.

### 2.8 `app/tools/`

**Purpose:** ad-hoc operator-facing CLIs. Each script must be runnable as
`python -m app.tools.<name>`.

Examples (planned):
- `app/tools/requeue.py` — re-enqueue a stuck job by ID.
- `app/tools/list_jobs.py` — query and pretty-print recent jobs.

These exist outside production runtime; they may import from anywhere.

### 2.9 `app/tests/`

**Purpose:** unit tests. Integration tests live under the `integration` marker.

```
app/tests/
├── conftest.py
├── test_callback_codec.py
├── test_config.py
├── test_enqueue_use_case.py
├── test_filenames.py
├── test_providers_youtube.py
├── test_temp_links.py
└── test_url_detection.py
```

Rules: see [`18-testing-strategy.md`](18-testing-strategy.md). Short version —
no real DB / Redis / network in unit tests; use fakes that implement the same
ABCs/protocols.

### 2.10 Top-level `app/*.py`

| File | Purpose |
|---|---|
| `composition.py` | Composition root (the *only* place that wires concrete infra) |
| `config.py` | `Settings` (pydantic-settings) + `get_settings()` |
| `exceptions.py` | `AppError` hierarchy |
| `logging_config.py` | structlog setup (JSON in prod, console in dev) |
| `main_bot.py` | Bot process entrypoint |
| `main_api.py` | API process entrypoint |
| `main_worker.py` | arq worker entrypoint |

These files are **part of the architecture**. They have stricter rules:
- `composition.py` is the only module that may import from both inner and
  outer layers.
- `main_*.py` files contain only startup wiring + signal handling. No logic.

---

## 3. `deploy/` — operations source

```
deploy/
├── compose/
│   ├── control.yml             # postgres, redis, migrate, bot, backup (no ports)
│   └── media.yml               # api, worker, cleanup, nginx, certbot (80/443 on nginx only)
├── single/
│   ├── docker-compose.yml      # include: control + media + single.override.yml
│   ├── single.override.yml     # networks, migrate deps, worker isolation
│   └── .env.example
├── nl1/
│   ├── docker-compose.yml      # include: control + nl1.overlay.yml (split, control plane)
│   ├── nl1.overlay.yml         # Postgres/Redis ports on NL1_PRIVATE_IP, internal api
│   └── .env.example
├── nl2/
│   ├── docker-compose.yml      # include: media (split, media plane)
│   └── .env.example
├── nginx/
│   ├── nginx.conf
│   ├── snippets/security.conf
│   └── conf.d/media.conf.template
├── certbot/
│   └── init-letsencrypt.sh
├── scripts/
│   ├── helpers.sh              # Sourced by all other scripts
│   ├── install.sh              # TUI installer/operator menu
│   ├── healthcheck.sh
│   ├── backup.sh
│   ├── restore.sh
│   ├── deploy_update.sh
│   ├── firewall_setup.sh
│   ├── cleanup.sh
│   ├── cookies_setup.sh        # Provider cookies → /srv/dwtgbot/secrets
│   └── certbot_init.sh
└── templates/
    └── env.template
```

Rules:
- Сервисы описываются только во фрагментах `deploy/compose/*`. Стеки
  (`single`, `nl1`, `nl2`) подключают их через `include` и добавляют только
  различия топологии (порты, сети, зависимости). Нужен Docker Compose ≥ 2.24.
- Скрипты работают со стеком через `compose_stack <single|nl1|nl2>` из
  `helpers.sh`, а не через жёстко прописанный путь.
- All bash scripts source `helpers.sh` and use `set -Eeuo pipefail`.
- Destructive commands (drop DB, force-recreate volumes) require an
  interactive `confirm` unless `ASSUME_YES=1` is set (used by CI).
- Per [`27-coding-standards.md`](27-coding-standards.md), every script must
  pass `bash -n` and `shellcheck --severity=warning`.

---

## 4. `docker/` — image definitions

```
docker/
├── bot.Dockerfile
├── api.Dockerfile
├── worker.Dockerfile
└── backup.Dockerfile
```

Rules:
- Multi-stage builds.
- Non-root `app:1000` user.
- `tini` as PID 1.
- `HEALTHCHECK` defined.
- Same base Python image across all four (one cache layer).
- See [`19-docker-architecture.md`](19-docker-architecture.md).

---

## 5. `migrations/` — Alembic

```
migrations/
├── env.py
├── script.py.mako
└── versions/
    └── 20260101_0000_0001_initial.py
```

Rules: see [`12-db-schema.md`](12-db-schema.md).
- One migration per logical change.
- Filename pattern: `YYYYMMDD_HHMM_NNNN_description.py`.
- **Never edit a migration that has been applied to a non-dev environment.**

---

## 6. `requirements/`

```
requirements/
├── base.txt    # Pinned production deps
├── dev.txt     # base + lint/test/types
└── prod.txt    # base (alias for clarity)
```

Rules:
- All versions pinned to exact (`==`).
- Bumps are PRs of their own; never silently piggy-backed onto feature PRs.
- `dev.txt` always starts with `-r base.txt`.

---

## 7. Dependency rules — the matrix

| **From** ↓  /  **To** → | `domain` | `application` | `infrastructure` | `bot` | `api` | `workers` | `utils` | `composition` |
|---|---|---|---|---|---|---|---|---|
| `domain` | — | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ pure only | ❌ |
| `application` | ✅ | — | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| `infrastructure` | ✅ | ✅ (protocols/DTOs only) | — | ❌ | ❌ | ❌ | ✅ | ❌ |
| `bot` | ✅ | ✅ | ❌ | — | ❌ | ❌ | ✅ | ❌ |
| `api` | ✅ | ✅ | ❌ | ❌ | — | ❌ | ✅ | ✅ (lifespan) |
| `workers` | ✅ | ✅ | ❌ | ❌ | ❌ | — | ✅ | ✅ (startup) |
| `utils` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | — | ❌ |
| `composition` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |

Legend:
- ✅ allowed
- ❌ forbidden
- ⚠️ allowed only for "pure" utilities with zero I/O or config dependencies

`utils` is special: it is the lowest dependency. It must not import anything
from `app/` other than `app/exceptions.py` (and even that should be rare).

---

## 8. Naming conventions

| Thing | Convention | Example |
|---|---|---|
| Module | `snake_case.py` | `process_download.py` |
| Class | `PascalCase` | `EnqueueDownloadUseCase` |
| Function | `snake_case` | `extract_first_url` |
| Constant | `SCREAMING_SNAKE_CASE` | `TELEGRAM_MAX_UPLOAD_BYTES` |
| Use case | `<Verb><Noun>UseCase` | `AnalyzeLinkUseCase` |
| Repository ABC | `<Noun>Repository` | `JobsRepository` |
| Repository impl | `SqlAlchemy<Noun>Repository` | `SqlAlchemyJobsRepository` |
| Service protocol | `<Noun>` (Protocol) | `Provider`, `QueueProducer` |
| DTO | `<Noun>(Input|Result|Payload|...)` | `EnqueueDownloadInput` |
| Domain entity | `<Noun>` | `DownloadJob` |
| ORM model | `<Noun>Model` | `DownloadJobModel` |
| Bash script | `snake_case.sh` | `deploy_update.sh` |
| Env variable | `SCREAMING_SNAKE_CASE` | `TEMP_LINK_TTL_SECONDS` |
| Doc file | `NN-kebab-case.md` (sequenced) | `12-db-schema.md` |
| ADR file | `NNNN-kebab-case.md` | `0003-redis-queue-choice.md` |

---

## 9. Where new files go — quick decision tree

```mermaid
flowchart TD
    start([New file?]) --> q1{Pure entity / enum / ABC?}
    q1 -- yes --> domain[app/domain/]
    q1 -- no --> q2{Use case orchestrating repos + services?}
    q2 -- yes --> app[app/application/use_cases/]
    q2 -- no --> q3{Protocol/DTO consumed by use cases?}
    q3 -- yes --> svc[app/application/services/<br/>or app/application/dto/]
    q3 -- no --> q4{Concrete adapter (DB / Redis / queue / yt-dlp / fs / telegram)?}
    q4 -- yes --> infra[app/infrastructure/<sub>/]
    q4 -- no --> q5{Telegram handler / keyboard / callback?}
    q5 -- yes --> bot[app/bot/]
    q5 -- no --> q6{FastAPI route?}
    q6 -- yes --> api[app/api/]
    q6 -- no --> q7{Long-running loop process?}
    q7 -- yes --> wk[app/workers/]
    q7 -- no --> q8{Pure helper, no I/O, no config?}
    q8 -- yes --> utils[app/utils/]
    q8 -- no --> q9{Operator CLI?}
    q9 -- yes --> tools[app/tools/]
    q9 -- no --> stop([Stop. You probably need an ADR.])
```

---

## 10. Anti-patterns (forbidden moves)

1. **Adding a new top-level `app/<package>/`** without an ADR.
2. **Mixing concerns inside an existing module** ("util" that secretly does
   I/O, "use case" that secretly opens a SQLAlchemy session).
3. **Cyclic imports between layers.** If you need to import "upward",
   refactor — extract a protocol or move shared types to `domain` or `utils`.
4. **Importing `composition.py` from anything other than `main_*.py`,
   `bot/application.py`, `api/app.py`, `workers/*.py`.**
5. **Adding deps in `requirements/dev.txt` that production also needs.**
   Production deps belong in `requirements/base.txt`.
6. **Editing files outside the scope of your change.** If your PR touches 50
   files, it is too large; split it.
7. **Renaming public modules / classes carelessly.** They appear in
   migrations, scripts, deploys. Use a deprecation shim or coordinate the
   rename.
8. **Putting documentation outside `docs/`.** No floating `*.md` in `app/`.

---

## 11. Common mistakes (and the fix)

| Mistake | Fix |
|---|---|
| Imported `redis.Redis` in a use case | Inject a service protocol; create the client in `composition.py` |
| Wrote a "domain service" because the use case got long | Split into multiple use cases or extract pure domain functions on the entity itself |
| Repository implementation is doing business logic | Move that logic to a use case; the repo should be CRUD + simple queries |
| Bot handler validating a URL with regex | Use `app/utils/url.py`; do not duplicate |
| Worker reading `os.environ` directly | Use `Settings` via the composition root |
| New SQLAlchemy model with no Alembic migration | Generate `alembic revision --autogenerate -m "…"`; review the diff before commit |
| New env var only in `deploy/nl1/.env.example` but not in `Settings` | Add to `app/config.py` first, then propagate to all `.env.example` (включая `deploy/single/.env.example`), then to `13-config-and-env.md` |
| Сервис добавлен прямо в `deploy/single/docker-compose.yml` или `deploy/nl*/docker-compose.yml` | Перенести во фрагмент `deploy/compose/{control,media}.yml`; в стеке оставить только `include` |
| Hard-coded port in compose file | Read from `.env` via `${VAR}` or expose a default in `Settings` |

---

## 12. Checklist for "where does this belong?"

Before creating a file, ask:

- [ ] Is there an existing module that could host this code? (Prefer extending.)
- [ ] If new, is the layer correct per the dependency matrix?
- [ ] Did I follow the naming conventions?
- [ ] Did I add the corresponding test in `app/tests/`?
- [ ] Did I update the relevant doc in `docs/`?
- [ ] If this introduces a new dependency, did I pin it and document why?
- [ ] If this introduces a new service or worker, is it wired in
      `app/composition.py` and the docker-compose stack?
