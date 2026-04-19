# ADR-0005 — Locked architectural assumptions (canonical register)

- **Status:** Accepted
- **Date:** 2026-04-19
- **Deciders:** project owner, original architect
- **Tags:** governance, architecture, foundations, lock, register
- **Supersedes:** —
- **Superseded by:** —

> 🔒 **This ADR is the canonical "do-not-change-without-cause" register
> for the project's foundational decisions.** Any PR that touches one of
> the items below requires a *new* ADR that explicitly supersedes the
> relevant section here.

---

## 1. Context

The project (`DWTGBot`) is a production-grade Telegram media bot. Over
its lifetime it will be touched by multiple humans and AI agents. Without
a single, prominent register of foundational decisions, three things go
wrong:

1. **Drift.** A reasonable-looking PR ("let me swap arq for Celery, it's
   more popular") quietly trades the system's operational profile.
2. **Re-litigation.** The same arguments ("why not Kubernetes?", "why
   Postgres for so few rows?") get re-opened every few months and consume
   review cycles.
3. **Plausibility traps for AI agents.** Without an explicit lock, an
   agent will pattern-match to "industry default" instead of "what this
   project actually committed to" and confidently propose changes that
   look fine but break operational assumptions, deployment automation,
   and runbooks.

Several decisions are already documented in their own ADRs
(`0001`–`0004`), but the **set as a whole** has not been declared as a
single coherent foundation. This ADR closes that gap: it enumerates
**twelve** locked assumptions, links to per-decision ADRs where they
exist, and defines the protocol for changing any of them.

### 1.1 Non-goals of this ADR

- It does **not** re-state the rationale for each decision in full —
  that lives in the per-decision ADRs and topic docs.
- It does **not** lock implementation details (specific Python
  micro-versions, specific Docker base image tags, specific yt-dlp
  versions). Those are dependency-management concerns, not architecture.
- It does **not** lock business/product direction; product-level locks
  live in [`docs/01-product-purpose.md`](../01-product-purpose.md) §8.

---

## 2. Decision

We **lock** the following twelve assumptions as the architectural
foundation of `DWTGBot`. Each can be changed only by a new ADR that
explicitly supersedes the corresponding row of §3.

| # | Locked assumption | Authoritative source(s) |
|---|---|---|
| 1 | **Language & runtime: Python 3.14+** (superseded from 3.11+ by [ADR-0009](0009-python-314-runtime.md)) | [ADR-0009](0009-python-314-runtime.md); [`27-coding-standards.md`](../27-coding-standards.md) §1 |
| 2 | **Two-server architecture** (control plane / media plane, separated by WireGuard private network) | [ADR-0001](0001-two-server-topology.md); [`02-architecture.md`](../02-architecture.md), [`19-docker-architecture.md`](../19-docker-architecture.md) |
| 3 | **NL-1 = bot + redis + postgres + backups** (control plane; no public ports) | [ADR-0001](0001-two-server-topology.md); [`19-docker-architecture.md`](../19-docker-architecture.md), [`20-deployment.md`](../20-deployment.md) |
| 4 | **NL-2 = worker + nginx + certbot + cleanup + storage volume** (media plane; public 80/443) | [ADR-0001](0001-two-server-topology.md); [`19-docker-architecture.md`](../19-docker-architecture.md), [`11-storage-strategy.md`](../11-storage-strategy.md) |
| 5 | **Provider-based design** for media platforms (`BaseProvider` interface; one concrete class per platform) | [`07-provider-architecture.md`](../07-provider-architecture.md); [`30-add-new-provider-guide.md`](../30-add-new-provider-guide.md) |
| 6 | **Redis** as the queue and short-lived state store (`arq` on top) | This ADR; [`09-queue-and-workers.md`](../09-queue-and-workers.md) |
| 7 | **PostgreSQL as the source of truth** for jobs, links, cache, audit (SQLAlchemy 2 + Alembic) | This ADR; [`12-db-schema.md`](../12-db-schema.md) |
| 8 | **Temp links for large files** (tokenized HTTPS URLs with TTL + use counter, served via Nginx `X-Accel-Redirect`) | [ADR-0004](0004-temp-links-via-nginx-x-accel.md); [`10-temp-links-and-delivery.md`](../10-temp-links-and-delivery.md) |
| 9 | **Structured JSON logging** (`structlog`, correlation IDs propagated via `contextvars`) | This ADR; [`14-logging-observability.md`](../14-logging-observability.md) |
| 10 | **Docker-first deployment** (Docker + Docker Compose v2; no bare-metal, no host-installed Python services) | This ADR; [`19-docker-architecture.md`](../19-docker-architecture.md), [`20-deployment.md`](../20-deployment.md) |
| 11 | **GitHub Actions for CI/CD** (lint + typecheck + test + image build + manual SSH-based deploy) | This ADR; [`21-cicd.md`](../21-cicd.md) |
| 12 | **Bash interactive installer** as the operator-facing entry point (`deploy/scripts/install.sh` with menu) | This ADR; [`20-deployment.md`](../20-deployment.md) |

These are **operationally coherent as a set.** Changing any one of them
likely cascades through several others — see §3.13 (interlocks) below.

### 2.1 What "locked" means in practice

- A PR that **violates** any locked assumption must be blocked at
  review until a superseding ADR is merged first.
- An AI agent that is asked to make such a change must:
  1. Recognise the lock (it is enumerated here);
  2. Refuse to write the code;
  3. Propose drafting a superseding ADR instead.
  This obligation is encoded in [`25-agent-guide.md`](../25-agent-guide.md)
  §3 and [`26-cursor-rules.md`](../26-cursor-rules.md) §5.
- "Locked" is **not** "frozen forever". It is "changeable only with a
  written, reviewed decision." The protocol for change is in §6 below.

---

## 3. Consequences

This section enumerates each lock individually: **the precise scope of
the lock**, what it implies, and the per-decision rationale snippet.

### 3.1 Python 3.14+ (superseded from 3.11+)

**Status:** superseded by [ADR-0009](0009-python-314-runtime.md)
(2026-04-19). Original 3.11+ scope is preserved below as historical
context; the binding contract now reads **CPython 3.14 or newer**.

**Scope of lock (current):** the project targets CPython 3.14 or newer
for all application code, scripts, and CI. We do not support 3.13 or
earlier.

**Why locked (current — see ADR-0009 for the full rationale):**
- 3.14 ships PEP 749 deferred annotations, t-strings, free-threaded
  builds and a stable JIT — features that materially affect typing,
  templating and worker concurrency choices we make in `app/`.
- The previous 3.11 floor was raised because pinned Rust/C-extension
  deps (pydantic-core, asyncpg, uvloop) now publish prebuilt cp314
  wheels, removing the build-from-source overhead that previously
  blocked the bump.
- Ubuntu 24.04 LTS images for Python now ship 3.14 in the
  `python:3.14.4-slim` family.

**Implications:**
- New code may freely use 3.12/3.13/3.14 features (`PEP 695` generic
  aliases, `Self`, `ExceptionGroup`, t-strings, etc.).
- We do **not** add `from typing import` shims for back-compat with
  3.11–3.13.

**Historical note (3.11+ era):** 3.11 was originally chosen as the
floor for `tomllib`, `Self`, `ExceptionGroup` and async perf. That
rationale still holds — 3.14 is a strict superset.

### 3.2 Two-server architecture

**Scope of lock:** the system runs on **exactly two** logical hosts —
NL-1 (control) and NL-2 (media) — connected by a private network. Not
one host, not three, not k8s.

**Why locked:** see [ADR-0001](0001-two-server-topology.md) for the
full rationale (blast-radius isolation, independent scaling, attack
surface minimization).

**Implications:**
- Internal state (Postgres, Redis) never gains a public IP.
- Cross-plane traffic uses the WireGuard subnet only.
- Single-host "dev compose" is allowed for local development but is
  *not* a supported deployment topology.

### 3.3 NL-1 = bot + redis + postgres (+ backups)

**Scope of lock:** these are the only application services that run on
NL-1. Specifically:
- `bot` (python-telegram-bot polling loop)
- `redis` (queue + short-lived state)
- `postgres` (source of truth)
- `backup` cron (pg_dump → off-host shipping)

**Why locked:** these services share two properties — *stateful* and
*never directly user-facing*. Co-locating them on a small private host
keeps the state machine simple, the backup story local, and the
firewall rules minimal.

**Implications:**
- Adding a new stateful service to NL-1 requires an ADR.
- **The bot must not mount `STORAGE_PATH`.** See
  [`11-storage-strategy.md`](../11-storage-strategy.md) §1.1.
- No public port is opened on NL-1 (`ufw` enforces this — see
  [`17-security.md`](../17-security.md)).

### 3.4 NL-2 = worker + nginx + certbot + storage

**Scope of lock:** NL-2 runs the media-traffic-heavy services and only
those:
- `worker` (arq worker; yt-dlp + ffmpeg)
- `api` (FastAPI; temp-link auth + healthchecks)
- `nginx` (HTTPS termination; `X-Accel-Redirect`)
- `certbot` (TLS renewal)
- `cleanup` (scheduled sweeper)
- a single storage volume mounted into the right subset of containers

**Why locked:** isolating the media plane lets us scale CPU/disk/bandwidth
independently of the control plane and keeps a yt-dlp/ffmpeg crash from
poisoning the bot or the database.

**Implications:**
- New media-related work (transcoding, packaging) belongs on NL-2.
- Public ports (80, 443) live only on NL-2.
- `worker` and `cleanup` are the only services with write access to
  the storage volume — see [`11-storage-strategy.md`](../11-storage-strategy.md) §1.1.

### 3.5 Provider-based design

**Scope of lock:** every supported media platform is implemented as a
concrete `BaseProvider` subclass (`app/infrastructure/providers/<name>.py`),
registered in `DefaultProviderRegistry`, and selected by the `Platform`
enum. The download pipeline talks only to the `BaseProvider` interface.

**Why locked:** this is the *single seam* that lets us add a new
platform as a small, reviewable PR without touching bot, queue, DB,
delivery, or operator tooling. Erode it and adding TikTok turns into a
horizontal change across the codebase.

**Implications:**
- Platform-specific quirks live entirely inside the provider.
- The use case (`enqueue_download_use_case`) does not branch on
  platform — it asks the provider.
- Adding a provider follows
  [`30-add-new-provider-guide.md`](../30-add-new-provider-guide.md);
  no ADR required for *adding* one (only for replacing the abstraction).

### 3.6 Redis as queue + short-lived state

**Scope of lock:** Redis is used for:
1. The job queue (via `arq`).
2. Short-lived state shared between the bot and itself (callback-data
   state store, idempotency keys).

It is **not** used as a primary data store, a relational substitute, or
a long-term cache that survives a restart.

**Why locked:**
- We already have it in the stack for the queue; adding a second queue
  technology (RabbitMQ, NATS) doubles operational surface for no clear
  win at our scale.
- `arq` is async-native, single-author, ~1 KLOC — reviewable.
- A restart-tolerant queue is unnecessary at our throughput.

**Implications:**
- Anything that *must* survive restarts goes to Postgres.
- Redis runs on NL-1 only; NL-2 connects over the private subnet.
- We do not introduce Redis Cluster / Sentinel / Streams without an
  ADR.

### 3.7 PostgreSQL as the source of truth

**Scope of lock:** the canonical record of every job, temp link,
cached metadata row, audit event, and runtime app-setting lives in
PostgreSQL. Schema is managed by SQLAlchemy 2 + Alembic.

**Why locked:**
- Strong typing, transactions, JSONB, and rich indexing in one engine
  cover every persistent need we have today and most we'll have for
  years.
- Migrations are the only sane way to evolve a schema across deploys;
  Alembic gives us reviewable diff + rollback.
- A single relational source of truth keeps the mental model small —
  there is *one* place to look for "what does the system believe right
  now".

**Implications:**
- New persistent state → Postgres table + Alembic migration. Not Redis,
  not "a JSON file in `STORAGE_PATH`".
- Editing an already-deployed migration is forbidden; write a new one.
- Dual-write patterns ("write to Redis and Postgres") require an ADR.

### 3.8 Temp links for large files

**Scope of lock:** files larger than `TELEGRAM_MAX_UPLOAD_MB` are
delivered through a tokenized HTTPS link with TTL and use-counter,
served via Nginx `X-Accel-Redirect` from an `internal` location.

**Why locked:** see [ADR-0004](0004-temp-links-via-nginx-x-accel.md).
Summary: keeps the worker fast, leaves streaming to nginx, holds auth
in Python, and never exposes the storage volume directly.

**Implications:**
- We do not return raw paths.
- We do not implement a different delivery channel (S3 redirect,
  pre-signed URL, peer-to-peer) without an ADR.
- TTL and use-counter are knobs, not removable features (see
  [`01-product-purpose.md`](../01-product-purpose.md) §8).

### 3.9 Structured JSON logging

**Scope of lock:** all application logs are emitted as one-JSON-object-per-line
through `structlog`, with stable event names, typed fields, and
correlation IDs (`request_id`, `job_id`, `user_id`, `chat_id`,
`update_id`) propagated via `contextvars`.

**Why locked:**
- One log shape across all services makes `jq`-driven runbooks
  possible (see [`14-logging-observability.md`](../14-logging-observability.md)
  and [`24-runbooks.md`](../24-runbooks.md)).
- Correlation IDs let an operator trace a single user request across
  bot → queue → worker → delivery in seconds.
- Plain `print` / unstructured logs would silently break `jq` filters
  and make incidents materially slower to resolve.

**Implications:**
- No `print`, no f-string log messages with secrets, no
  ad-hoc loggers.
- New log events are added to the catalogue in
  [`14-logging-observability.md`](../14-logging-observability.md).
- Switching the log shape (e.g. logfmt, OpenTelemetry export) requires
  an ADR.

### 3.10 Docker-first deployment

**Scope of lock:** every long-running service in production is a Docker
container, orchestrated by Docker Compose v2. There are no host-installed
Python services, no virtualenvs running under systemd, no pip on the
target host.

**Why locked:**
- One artefact format (the image) is the only thing that crosses
  CI → registry → host.
- Reproducible runtime: the same image runs locally and in prod.
- Compose v2 is the smallest viable orchestrator at our scale; k8s
  would be operationally heavier than the rest of the system combined.

**Implications:**
- New services are added as containers in the relevant `docker-compose.yml`,
  not as systemd units.
- Image build happens in CI ([`21-cicd.md`](../21-cicd.md)); host-side
  rebuilds are forbidden in prod.
- Switching to Kubernetes / Nomad / k3s requires an ADR.

### 3.11 GitHub Actions for CI/CD

**Scope of lock:** all automated checks and image builds run in GitHub
Actions. Deploys are triggered manually from the same workflow set
(SSH-based; idempotent).

**Why locked:**
- The repo already lives on GitHub; a separate CI provider (CircleCI,
  Jenkins) duplicates auth/secrets surface for no win.
- Free tier covers our usage.
- All CI configuration lives next to the code (`.github/workflows/`),
  so changes are reviewable in the same PR.

**Implications:**
- New automated checks → a workflow file under `.github/workflows/`.
- Secrets live in GitHub Actions secrets (not in the repo, not on
  hosts).
- Replacing GHA (with GitLab CI, Buildkite, etc.) requires an ADR.

### 3.12 Bash interactive installer

**Scope of lock:** the operator-facing setup and day-2 operations entry
point is `deploy/scripts/install.sh` — a bash script with an interactive
menu (and idempotent non-interactive mode for CI). Not Ansible, not
Terraform, not a custom Python CLI.

**Why locked:**
- Bash is present on every Ubuntu 24.04 install; no bootstrapping
  needed.
- The install/restore/backup/cleanup flow is essentially "run a few
  commands in order and ask the operator to confirm destructive ones" —
  bash is the right size for this.
- A higher-level tool (Ansible) would be reasonable later but adds a
  dependency we don't want to require for a fresh install.

**Implications:**
- All operator scripts use `bash` strict mode (`set -Eeuo pipefail`)
  and are validated by `shellcheck` in CI.
- New operational flow → a function in `install.sh` (or a sibling
  script in `deploy/scripts/`), wired into the menu.
- Replacing bash with Ansible/Terraform requires an ADR.

### 3.13 Interlocks between locks

The twelve assumptions are not independent. Common cascades:

| If you change... | You'll likely also be forced to change... |
|---|---|
| #2 (two-server) | #3, #4, #10, #12 (compose layout, installer flow, deployment topology) |
| #6 (Redis queue) | #5 (provider seam may shift), #9 (logged events change), runbooks 2 & 5 |
| #7 (Postgres SoT) | #6 (state ownership shifts), all migrations, healthchecks, backups |
| #8 (temp links) | #4 (NL-2 surface), Nginx config, security model |
| #10 (Docker-first) | #11 (CI/CD), #12 (installer) |

So the right way to read this register: changing *any* row is
expensive; changing one rarely stays "one".

---

## 4. Alternatives considered

### 4.1 Document each lock only in its per-decision ADR

We could rely on `0001`–`0004` and add ADRs for the remaining items
piecewise. We rejected this because **there was no single "are these
locked?" answer.** Without one register, an AI agent reading
`28-implementation-playbook.md`'s "🔒 LOCKED" list had to trust the
list was authoritative; now it can verify against a single ADR that
also defines what "locked" means in practice.

### 4.2 Embed the lock register in `docs/00-overview.md`

The overview already lists the tech stack. Embedding the lock there
would mix "what we use today" with "what we have decided to keep
using." We kept the overview as a friendly description and put the
contractual statement in an ADR.

### 4.3 No formal lock — rely on review

This is what most projects do, and it works in small teams where
context is in everyone's heads. With AI agents writing code, this
fails: the agent has no shared memory between sessions, so absent an
ADR it will pattern-match to "what most projects do" and break our
operational profile.

---

## 5. Compliance

How the team / agents notice when this ADR is being violated.

### 5.1 In code review

A PR is non-compliant if it does any of:

- Adds a new long-running service outside Docker (lock 10).
- Adds a host-installed Python venv to a production target (lock 10).
- Adds a stateful service to NL-2 or a public port to NL-1 (locks 3, 4).
- Mounts `STORAGE_PATH` into the `bot` service (lock 3).
- Stores authoritative state in Redis (lock 7).
- Uses unstructured `print(...)` for application logging (lock 9).
- Replaces `arq` with another queue (lock 6).
- Branches on `Platform` outside `app/infrastructure/providers/`
  (lock 5 — see [`07-provider-architecture.md`](../07-provider-architecture.md) §6).
- Returns raw filesystem paths to clients (lock 8).
- Adds CI in a system other than GitHub Actions (lock 11).
- Replaces the bash installer with another tool (lock 12).

Each of these requires a superseding ADR before merge.

### 5.2 In docs

The following docs reference this ADR and must stay consistent with it:

- [`docs/00-overview.md`](../00-overview.md) §3, §4 (topology + stack
  table).
- [`docs/02-architecture.md`](../02-architecture.md) (layered model +
  topology).
- [`docs/19-docker-architecture.md`](../19-docker-architecture.md)
  (compose stacks, mounts).
- [`docs/20-deployment.md`](../20-deployment.md) (installer +
  Compose-only deployment).
- [`docs/21-cicd.md`](../21-cicd.md) (GHA workflows).
- [`docs/25-agent-guide.md`](../25-agent-guide.md) §3 (architecture
  rules).
- [`docs/26-cursor-rules.md`](../26-cursor-rules.md) §5 (architecture
  preservation).
- [`docs/28-implementation-playbook.md`](../28-implementation-playbook.md)
  §5 (locked-decisions list).

If you change this ADR (via supersession), update all of the above in
the same PR.

### 5.3 In tooling (signal-level)

- `ruff` + `mypy` + `pytest` enforce the *code-level* expectations of
  most locks (e.g. no `print`, no shell strings).
- `shellcheck` enforces script quality (lock 12).
- The `Settings` model in `app/config.py` enforces the runtime contract
  (env vars typed, fail-fast on missing required values).
- There is no automated check that catches "swapped Redis for X" — that
  is a review-level signal, which is why this ADR exists.

---

## 6. Process for changing a locked assumption

To overturn or modify any row of §2:

1. **Open a draft ADR** (`docs/adr/NNNN-<short-title>.md`) with
   `Status: Proposed`. The title must reference what's being changed
   (e.g. `0009-replace-redis-with-rabbitmq.md`).
2. In the ADR's `Supersedes` field, name this ADR **and** the row of §3
   you are overturning (e.g. "Supersedes ADR-0005 row 6 (Redis as queue)").
3. Spell out the cascade per §3.13 — what other locks the proposed
   change forces you to revisit.
4. Get reviewer agreement before writing application code. PRs that
   change a locked area without a Proposed-or-better ADR are blocked
   at review.
5. On merge of the new ADR (`Status: Accepted`), edit *this* ADR's
   §2 table to mark the relevant row with
   `~~Locked~~ → see ADR-NNNN` (do not delete the row; preserve
   history).

This protocol applies equally to humans and AI agents.

---

## 7. References

- Related ADRs:
  - [ADR-0001 — Two-server topology](0001-two-server-topology.md)
  - [ADR-0002 — `python-telegram-bot`](0002-python-telegram-bot.md)
  - [ADR-0003 — `ruff format` (no `black`)](0003-ruff-format-no-black.md)
  - [ADR-0004 — Temp links via Nginx `X-Accel-Redirect`](0004-temp-links-via-nginx-x-accel.md)
- Topic docs (cross-referenced in §5.2): `00`, `02`, `19`, `20`, `21`,
  `25`, `26`, `28` under [`docs/`](../).
- External:
  - [Architecture Decision Records (Michael Nygard, 2011)](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
  - [12-factor app](https://12factor.net/) — informs locks 9, 10, 11.

---

## 8. History

| Date | Status | Note |
|---|---|---|
| 2026-04-19 | Proposed | Drafted to consolidate the twelve foundational decisions into a single canonical lock register. |
| 2026-04-19 | Accepted | Foundation of the project; supersedes the informal "🔒 LOCKED" list previously inlined in `28-implementation-playbook.md` §5. |
