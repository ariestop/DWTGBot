# 25 — Agent guide (operating manual for AI agents)

> Status: Stable
> Audience: AI coding agents (Cursor, Claude, Codex, Copilot Workspace,
> bespoke pipelines) and the humans who work through them.
> Read next: [`26-cursor-rules.md`](26-cursor-rules.md) (strict
> operating protocol), [`27-coding-standards.md`](27-coding-standards.md),
> [`28-implementation-playbook.md`](28-implementation-playbook.md).

> ⚠️ **Read this BEFORE you write any code in DWTGBot.**
>
> If you skip this document, you will, statistically:
> - put business logic in the wrong layer,
> - forget the migration / docs / tests update,
> - break the temp-link contract or the queue idempotency,
> - and create a PR that has to be rewritten.
>
> The guide is long because every section is paid for in past
> production incidents.

---

## 1. Purpose of this guide

DWTGBot is a multi-process, multi-server system: a Telegram bot, a
queue, a worker that drives external binaries (`yt-dlp`, `ffmpeg`),
a public HTTPS surface for large-file delivery, and a small
operations toolkit. It looks simple — "download a video, send it" —
and it is **not** simple under the hood: there are queue
guarantees, security boundaries, retention policies, and locked
architectural decisions that are easy to violate by accident.

This guide answers four questions for an AI agent:

1. **How do I think about this project?** (mental model)
2. **What am I forbidden to change without permission?** (locked decisions)
3. **In what order do I make a change safely?** (protocol)
4. **How do I know the change is done?** (definition of done)

Everything else in `docs/` is **specification** — what the system
does. This file is the **operating manual** for *making changes to
that specification's implementation*.

If you are running as Cursor specifically, also read
[`26-cursor-rules.md`](26-cursor-rules.md), which is shorter and
strictly operational.

> 🧭 **Relationship to `26-cursor-rules.md`.** `25-` (this document)
> is the canonical source for the **why**: principles, layers,
> protocol, escalation reasoning. `26-` is the *operational
> projection* — Cursor-specific "do this / don't do that" derived
> from the same principles. Whenever the two could disagree, **`25-`
> wins on the why; `26-` wins on the what-to-do step**. The exact
> per-section authority map lives in
> [`26-` Appendix C](26-cursor-rules.md#appendix-c--cross-reference--deduplication-map).
> Before extending an explanation in `26-`, check that map — most
> topics belong here, with only a one-line projection there.

---

## 1.A Mandatory principles (P1–P11) — canonical register

These are the **eleven principles** every change in DWTGBot is
graded against. They are referenced by code (P1…P11) throughout
this guide and `26-cursor-rules.md`. The codes are **stable** —
do not renumber, do not drop, do not rephrase silently.

| Code | Principle | One-line meaning | Primary enforcement |
|---|---|---|---|
| **P1** | **Minimal safe scope** | Smallest diff that fully solves the stated goal; no extras. | §6 protocol, §6.A plan ("Files NOT touched"), §17.A mid-edit signals |
| **P2** | **Architecture first** | Decide layer + ADR-0005 impact **before** writing code; the structure is the contract. | §2.3 layers, §3.1 dep direction, §3.9 ADR-0005 |
| **P3** | **No unrelated changes** | One PR = one concern; drive-by edits are forbidden. | §17 anti-patterns, §18 common mistakes |
| **P4** | **Docs / tests / config travel together** | Every behaviour change ships with its docs, tests, and config in the same PR. | §3.8, §13.1, §14, §19.4, §19.5 |
| **P5** | **No infrastructure leakage into domain** | `app/domain/` and `app/application/` never import drivers, frameworks, or env. | §3.1, §3.3, §8.2, §8.3 |
| **P6** | **No business logic in handlers** | Bot/API/worker handlers route — they do not orchestrate, query, or compute. | §3.2, §8.1, §8.5, §8.6 |
| **P7** | **Queue semantics preserved** | `_job_id` determinism, bounded retries, idempotent side-effects, stateless workers. | §3.4, §11, §19.5 |
| **P8** | **Provider contracts explicit** | Every change goes through the `BaseProvider` surface; no platform-specific leaks outside the provider. | §3.5, §10 |
| **P9** | **Deploy changes require idempotency reasoning** | Every `deploy/scripts/*.sh` and compose change is justified for re-run safety. | §3.6, §12, §19.5 |
| **P10** | **DB changes require migration reasoning** | No schema change without an Alembic migration **and** a written rollback / deploy-order story. | §3.7, §9, §19.5 |
| **P11** | **Security defaults never weakened** | TLS, HSTS, firewall, `internal` Nginx, `ensure_within`, `sanitize_filename`, secrets-via-`Settings` are floors, not suggestions. | §3.9, §16, §19.6 |

### How the principles are used in practice

- **Plan (§6.A)** — agent declares which Ps the change touches.
- **Mid-edit signals (§17.A)** — each smell is tagged with the
  violated P; tripping it means the principle was about to break.
- **Anti-patterns (§17)** — each entry maps back to one or more Ps.
- **Common mistakes (§18)** — each row carries the principle it
  violates so the cure is unambiguous.
- **Definition of done (§19)** — closes with an explicit
  "P1–P11 compliance" pass.
- **Refusal templates (§20.6 / `26-` §17.9)** — invoked when the
  request would force a P-violation the agent cannot accept.

If you cannot map your change onto P1–P11, you have not understood
the change yet. Stop and re-read.

> The same register, with identical codes and labels, is mirrored
> in [`26-cursor-rules.md`](26-cursor-rules.md) §1.A. The two must
> stay in lockstep — if you edit one, edit the other in the same
> PR (P4).

---

## 2. Project mental model

### 2.1 The system in one paragraph

A user sends a YouTube/Instagram URL to a Telegram bot. The bot
analyzes the link (via `yt-dlp` metadata), shows download options,
and on selection enqueues a job. A worker on a different server
performs the download and either uploads the result back to Telegram
(small files) or generates a tokenised HTTPS link (large files). A
periodic cleanup removes expired files and links. Postgres is the
system-of-record; Redis is the queue and a short-lived state store;
Nginx terminates TLS and serves big files via `X-Accel-Redirect`.

### 2.2 Two planes (locked, see ADR-0001)

| Plane | Server | Components | Public exposure |
|---|---|---|---|
| **Control plane** | NL-1 | bot, Redis, Postgres, backup scheduler | none |
| **Media plane**   | NL-2 | worker, Nginx, Certbot, cleanup, file storage | 80/443 |

Communication NL-1 ↔ NL-2 is over a private network only. Public
attackers can reach **only NL-2**, and only through Nginx.

This split is the foundation of the security model. Changes that
weaken it (moving Postgres to NL-2, opening ports on NL-1, putting
Nginx on NL-1) are **forbidden without an ADR**.

### 2.3 Layered model (hexagonal-lite)

Dependencies always point **inward**. Outer layers depend on inner
layers; never the reverse.

```
deploy/scripts ──┐
docker/        ──┼───► (operational; not imported by app code)
                  │
app/bot/      ───┐
app/api/      ───┤───► app/application/  ───► app/domain/
app/workers/  ───┘                  ▲
                                    │
            app/infrastructure/  ───┘  (implements protocols
                                       defined in application/domain)
```

| Layer | What lives here | Imports from |
|---|---|---|
| `app/domain/` | Pure entities, enums, repository protocols, invariants | nothing project-specific |
| `app/application/` | Use cases, orchestration services, DTOs, protocols for infra | `app/domain/`, stdlib |
| `app/infrastructure/` | DB, Redis, Telegram client, yt-dlp, ffmpeg, storage, queue impl | `app/application/`, `app/domain/`, third-party |
| `app/bot/` | Telegram handlers, keyboards, callbacks | `app/application/`, `app/utils/`, `python-telegram-bot` |
| `app/api/` | FastAPI routes (internal `/healthz`, `/readyz`; public `/d/{token}`) | `app/application/`, `app/infrastructure/` (via `composition.py`) |
| `app/workers/` | arq runtime, scheduled tasks, cleanup, backup | `app/application/`, `app/infrastructure/` (via `composition.py`) |
| `app/composition.py` | **Single** wiring point of concrete infra into use cases | every layer above |
| `deploy/`, `docker/` | Operations: Compose, Nginx, Certbot, scripts | none (it's not Python) |

### 2.4 Source-of-truth precedence

When two sources disagree, the higher one wins:

1. **ADRs** (`docs/adr/*.md`) — hard architectural locks.
2. **Topic docs** (`docs/02-…` to `docs/33-…`) — design and
   conventions.
3. **Code** — the running truth (but if it contradicts an ADR or
   topic doc, the **code is the bug**).
4. **Inline comments** — local context, never spec.
5. **Root `README.md`** — marketing/overview only.

If you're rewriting based on an inline comment that disagrees with
a doc, **stop and re-read the doc**.

### 2.5 Critical and fragile components (the blast-radius map)

| Component | If you break it… | Fragility | First read |
|---|---|---|---|
| `app/composition.py` | every entry point fails to start | medium | [`02-architecture.md`](02-architecture.md) |
| `app/config.py` (`Settings`) | startup fails, or worse — silent misconfig | high | [`13-config-and-env.md`](13-config-and-env.md) |
| `app/exceptions.py` (`AppError` hierarchy) | wrong errors shown to users; logs break | medium | [`16-error-handling.md`](16-error-handling.md) |
| `app/infrastructure/db/models.py` + `migrations/` | schema drift, data loss potential | very high | [`12-db-schema.md`](12-db-schema.md) |
| `app/infrastructure/queue/*` (idempotency `_job_id`) | duplicate downloads, lost jobs | high | [`09-queue-and-workers.md`](09-queue-and-workers.md) |
| `app/utils/filenames.py` (`ensure_within`, `sanitize_filename`) | path traversal vulnerability | very high | [`11-storage-strategy.md`](11-storage-strategy.md), [`17-security.md`](17-security.md) |
| `app/api/public/downloads.py` | auth bypass on public link | very high | [`10-temp-links-and-delivery.md`](10-temp-links-and-delivery.md) |
| `app/infrastructure/providers/*` | downloads break for one platform | medium (isolated) | [`07-provider-architecture.md`](07-provider-architecture.md) |
| `deploy/nginx/*` | TLS / public link breakage | high | [`10-temp-links-and-delivery.md`](10-temp-links-and-delivery.md) |
| `deploy/scripts/install.sh` / `deploy_update.sh` / `restore.sh` | non-idempotent ops, broken servers | high | [`20-deployment.md`](20-deployment.md), [`24-runbooks.md`](24-runbooks.md) |
| `.github/workflows/*` | broken CI/CD, accidental release | medium | [`21-cicd.md`](21-cicd.md) |

Treat anything in this table as **read-twice-write-once**.

---

## 3. Non-negotiable architecture rules

These are **constraints**, not suggestions. A change that violates
any of them must come with an ADR.

### 3.1 Layer dependency direction

| Forbidden import | Why |
|---|---|
| `app/domain/*` ← `app/infrastructure/*` | domain must stay framework-free |
| `app/domain/*` ← `app/application/*` | domain has no callers, only callees |
| `app/application/*` ← `app/infrastructure/*` | application talks to protocols, not impls |
| `app/application/*` ← `app/bot/*` / `app/api/*` / `app/workers/*` | use cases must not know about transports |
| `app/bot/*` ← `app/api/*` / `app/workers/*` | sibling layers must not bind |
| `app/utils/*` ← anything project-specific | utils must stay pure |

`composition.py` is the only place that imports from every layer
and wires them together.

### 3.2 Bot handlers contain no business logic

A handler may:
- read `Update` / `CallbackQuery`,
- call **one** use case on `BotContainer`,
- format the response (text, keyboard),
- bind correlation context, log, handle exceptions per the
  three-tier pattern.

A handler may **not**:
- query the DB or Redis directly,
- call `yt-dlp` or any provider,
- compute file paths,
- enqueue anything by hand (use the producer service),
- perform multi-step orchestration.

### 3.3 Domain is framework-free

`app/domain/` may import only the standard library.
- No SQLAlchemy.
- No pydantic (use plain `@dataclass(slots=True)`).
- No telegram, no httpx, no redis, no arq.
- No `app.config` (configuration is environmental, not domain).

If you need to know "is this URL valid?", that's an application or
utility concern, not domain.

### 3.4 Workers are stateless and idempotent

- A worker may be killed at any moment; the next worker must be
  able to pick up where the previous left off (or start over
  cleanly).
- No in-process caches whose loss changes correctness.
- Every job has a deterministic `_job_id` so arq deduplicates
  retries.
- Partial work (a half-downloaded file) lives in a job-scoped
  scratch directory and is cleaned up by the cleanup worker if the
  job dies.

### 3.5 Providers isolate platform quirks

- `BaseProvider` defines the contract: `get_info`, `build_options`,
  `download`.
- All `yt-dlp` and platform-specific format strings live inside the
  provider for that platform.
- Application and worker code must remain provider-agnostic — they
  call methods on `BaseProvider`, not on `YouTubeProvider`.

### 3.6 Deploy scripts are idempotent

- Re-running any `deploy/scripts/*.sh` step on a healthy server
  must not break it.
- `set -Eeuo pipefail` at the top.
- Destructive operations (`restore.sh`, `firewall_setup.sh --apply`)
  prompt for explicit confirmation.

### 3.7 Schema changes always go through Alembic

- No `CREATE TABLE` / `ALTER TABLE` outside `migrations/versions/`.
- Auto-generated migrations are **reviewed by hand**, not blindly
  committed.
- Enum value additions to PostgreSQL ENUMs require an explicit
  `ALTER TYPE … ADD VALUE`.
- Backwards-incompatible migrations need a 2-step plan
  (add → backfill → drop).

### 3.8 Tests, docs, logging move with the code

- Behavior change → tests update in the **same PR**.
- Public surface change (env var, command, callback, table column,
  log event) → docs update in the **same PR**.
- New code path that an on-call engineer would care about → log
  event added to [`14-logging-observability.md`](14-logging-observability.md)
  catalogue.

### 3.9 Locked decisions can only change via ADR

The **canonical register of locked decisions** is
[**ADR-0005 — Locked architectural assumptions**](adr/0005-locked-architectural-assumptions.md).
It enumerates twelve foundational rows (Python 3.14+ — see ADR-0009; two-server
architecture, NL-1/NL-2 service composition, provider-based design,
Redis queue, Postgres SoT, temp links, structured logging, Docker-first,
GitHub Actions, bash installer) and defines what "locked" means in
practice (§2.1) and the supersession protocol (§6).

If your change touches any row in ADR-0005 §2:
1. **Stop coding.** Draft a new ADR (`docs/adr/000N-<slug>.md`) that
   explicitly supersedes the relevant row of ADR-0005 §3.
2. Spell out the cascade per ADR-0005 §3.13 — what other locks the
   change forces you to revisit.
3. Get reviewer agreement on the ADR before any application code lands.
4. On merge, update ADR-0005 §2 to mark the row as superseded (do not
   delete; preserve history).

If you're not sure whether something is locked, **ask** before changing
it. Default assumption: if it's listed in ADR-0005 §2, it's locked.

---

## 4. Required reading order before any change

### 4.1 Universal floor (always read these)

These four take ~10 minutes total and prevent the majority of
broken PRs:

1. [`docs/README.md`](README.md) — index + conventions.
2. [`docs/00-overview.md`](00-overview.md) — what the system is and is
   not.
3. [`docs/02-architecture.md`](02-architecture.md) — layers and data
   flow.
4. [`docs/03-project-structure.md`](03-project-structure.md) —
   dependency matrix.
5. This file (`docs/25-agent-guide.md`).
6. [`docs/26-cursor-rules.md`](26-cursor-rules.md) (if running as
   Cursor).

### 4.2 Topic-specific lookup

| If you're touching… | Read first |
|---|---|
| Bot UX (commands, buttons, messages) | [`06-bot-flow.md`](06-bot-flow.md) |
| A use case | [`02-architecture.md`](02-architecture.md), [`16-error-handling.md`](16-error-handling.md) |
| A provider | [`07-provider-architecture.md`](07-provider-architecture.md), [`30-add-new-provider-guide.md`](30-add-new-provider-guide.md) |
| Worker / arq tasks | [`09-queue-and-workers.md`](09-queue-and-workers.md) |
| Download pipeline (yt-dlp / ffmpeg) | [`08-download-pipeline.md`](08-download-pipeline.md) |
| Temp links / public delivery | [`10-temp-links-and-delivery.md`](10-temp-links-and-delivery.md) |
| Storage layout / cleanup | [`11-storage-strategy.md`](11-storage-strategy.md), [`23-cleanup-retention.md`](23-cleanup-retention.md) |
| DB schema | [`12-db-schema.md`](12-db-schema.md) |
| Config / env vars | [`13-config-and-env.md`](13-config-and-env.md) |
| Logging | [`14-logging-observability.md`](14-logging-observability.md) |
| Healthchecks | [`15-healthchecks.md`](15-healthchecks.md) |
| Errors | [`16-error-handling.md`](16-error-handling.md) |
| Security | [`17-security.md`](17-security.md) |
| Tests | [`18-testing-strategy.md`](18-testing-strategy.md) |
| Docker images | [`19-docker-architecture.md`](19-docker-architecture.md) |
| Deploy / install | [`20-deployment.md`](20-deployment.md) |
| CI/CD | [`21-cicd.md`](21-cicd.md) |
| Backups | [`22-backup-restore.md`](22-backup-restore.md) |
| Incidents | [`24-runbooks.md`](24-runbooks.md), [`31-troubleshooting.md`](31-troubleshooting.md) |

### 4.3 Reading budget triage

| Change size | Minimum reading |
|---|---|
| Typo / docstring | universal floor (skim) |
| Single-file bug fix | floor + topic doc + the file's tests |
| Single-feature addition | floor + topic doc + adjacent docs (e.g. config + logging) |
| Cross-cutting change (touches >5 files or >2 layers) | full floor + every relevant topic doc + ADR check |
| Infra / deploy change | floor + 19 + 20 + 21 + relevant runbook |
| DB / queue / security change | floor + topic doc + ADR check + **stop, write a plan, get it reviewed** |

If you can't tell what reading budget applies, default upward.

### 4.4 Red flags in the user's request (auto-detect before planning)

These are the phrases that historically cost the most rework when an
agent took them at face value. If any appear in the request, **pause
before classifying** and react per the right column. The companion
operational protocol for Cursor is in
[`26-cursor-rules.md`](26-cursor-rules.md) §3.8 (with the same table
phrased as a hard checklist).

| Trigger phrase (RU / EN) | What it usually hides | Agent's required reaction |
|---|---|---|
| "просто", "по-быстрому", "just", "quickly" | a request the user thinks is small but spans ≥2 layers | classify per §5; never assume "small" before §7 review |
| "заодно…", "while you're here…" | drive-by edits incoming | refuse to bundle; surface as a follow-up task |
| "временно", "for now", "пока что" | tech debt with no owner | require a tracked TODO with owner + issue, or refuse |
| "почисти", "refactor this", "clean up" | speculative refactor | demand explicit goal/contract; reject "while-here" cleanups |
| "перепиши", "rewrite this" | broad rewrite request | force §6 design note before any code |
| "поменяй очередь / БД / язык / framework" | locked-decision touch | check ADR-0005 §2; if locked → require superseding ADR first |
| "забей на миграцию", "skip the migration" | schema drift recipe | refuse; §9 is non-negotiable |
| "не пиши тесты", "skip tests" | quality regression | refuse; §3.8 is non-negotiable |
| "захардкодь", "вставь токен в код" | secret leak | hard refuse; secrets via `Settings` only (see §16) |
| "сделай как на проде сейчас" | repo-vs-live drift | refuse to mirror live drift; fix repo first, redeploy |
| "обнови env прямо на сервере" | config drift | refuse; PR the change, then redeploy |
| "и заодно поправь ещё N мест" | bundling unrelated work | split into separate PRs |
| "не читай документацию" | skipping required reading | refuse; §4.1 floor is mandatory |
| "выкатывай в main", "skip review" | bypass review | refuse; out of agent authority |

If a request triggers two or more red flags, the agent must respond
with a clarification or refusal (templates in §20.6) **before**
producing a plan. Acting without that step produces PRs that get
rewritten.

---

## 5. Task classification before implementation

Classify the request **before** writing code. Use this taxonomy:

| Class | Examples | Risk | Mandatory checks |
|---|---|---|---|
| **A. New feature (additive)** | new bot command, new provider, new option | low–medium | tests, docs, no behavior change to existing flows |
| **B. Bug fix** | wrong error message, missed retry, bad fallback | low | tests reproducing the bug; minimal diff |
| **C. Refactor (no behavior change)** | rename module, extract function | low | unchanged behavior is *the* invariant; tests must pass without modification |
| **D. Infrastructure** | Docker, Compose, Nginx | medium | idempotency, healthchecks, runbook update |
| **E. DB migration** | new column / index / table | high | alembic, model, repo, docs, rollback thinking |
| **F. Provider change** | yt-dlp version bump, format spec | medium | provider tests, fakes, real-link smoke test |
| **G. Queue/worker change** | new task, retry policy, idempotency key | high | idempotency contract, retry semantics, partial-failure path |
| **H. Deploy / scripts** | install.sh, deploy_update.sh, certbot_init.sh | high | idempotent, prompts for destructive ops, shellcheck clean |
| **I. Observability** | new log event, new metric, healthcheck | low | event in catalogue, no PII, level discipline |
| **J. Security** | secret handling, input validation, exposure | very high | ADR if changing surface; threat model in [`17-security.md`](17-security.md) |
| **K. Documentation only** | clarify a doc, fix a link | very low | source-of-truth precedence respected |

Per class, the table above is the **floor**. Always also follow §6.

If a request spans multiple classes, decompose it into smaller PRs
(see §3.8 in [`27-coding-standards.md`](27-coding-standards.md)
about small reviewable PRs).

---

## 6. Safe change protocol

A 12-step procedure. Skipping any step costs more than running it.

1. **Restate the goal** in one sentence. If you can't, the request
   is ambiguous — ask.
2. **Classify** per §5.
3. **Identify affected layers and files.** Use the dependency
   matrix; resist the temptation to just `grep`.
4. **Read the relevant docs** per §4.
5. **Read the relevant ADRs.** If the change touches a locked
   decision, stop and write an ADR before coding.
6. **Plan in 3–10 bullets.** Files to add, modify, delete; order;
   tests; docs; migrations; env vars; deploy impact. Use the
   verbatim template in §6.A — agents that skip the template skip
   the discipline.
7. **Minimize blast radius.** Can the change be smaller? Can it
   land in two PRs (refactor first, behavior change second)?
8. **Implement layer-by-layer, inside-out.** Domain → application →
   infra → entry layer (bot/api/worker) → composition → tests →
   docs.
9. **Run linters and tests locally** (`ruff format && ruff check &&
   mypy app && pytest -m "not integration"`).
10. **Update docs and migrations** in the same change.
11. **Self-review against the checklists** in §17 and §19.
12. **Open the PR with a clear description**: what, why, files,
    migrations, docs, risk, test plan.

### 6.A Plan template (verbatim — emit before any file edit)

The plan is not optional and not narrative. Emit it in this exact
shape; a reviewer should be able to lift it into the PR description
unchanged.

```markdown
### Plan

- **Goal:** <one sentence>
- **Class (per §5):** <A | B | C | D | E | F | G | H | I | J | K>
- **Principles in play (§1.A):** <P1, P3, P4, … — list every P this change touches; "none" only for pure typo fixes>
- **ADR-0005 §2 row touched?** <No | Yes — row N → STOP, draft superseding ADR (P2)>
- **Reading done:** README, 00, 02, 03, 25, 26, ADR-0005 §2, <topic doc(s)>
- **Files to ADD:**
  - `path/to/new_file.py` — <why>
- **Files to MODIFY:**
  - `path/to/existing.py` — <one-line diff intent>
- **Files to DELETE:** <none | path — why>
- **Files / layers explicitly NOT touched:** (P1 + P3 enforcement)
  - <e.g. `app/bot/handlers/` — this is a use-case-only change>
- **Migration?** (P10) <No | Yes — `migrations/versions/NNNN_<slug>.py`, deploy order: …>
- **Env var?** (P4) <No | Yes — `<NAME>` added in: config.py, .env.example, deploy/nl{1,2}/.env.example, compose env block, 13- doc>
- **Tests:** (P4) <new file(s) + which conditions; existing tests expected to stay green>
- **Docs to update in this PR:** (P4) <list of `docs/*.md` per §13.1>
- **Queue / worker contract impact?** (P7) <No | Yes — what changes in `_job_id`, retries, status, idempotency, and how it stays compatible>
- **Provider contract impact?** (P8) <No | Yes — which method on `BaseProvider`, how the surface stays stable>
- **Deploy / script idempotency note?** (P9) <N/A | how re-running stays safe; what destructive op is gated by `confirm`>
- **Security defaults touched?** (P11) <No | Yes — what is weakened/strengthened, with justification>
- **Risks / blast radius:** <what could break; rollback story>
- **Verification:** <commands to run + manual smoke if any>
```

Hard rules about the plan:

- **No prose answers.** Bullets and lists only.
- **No `TBD`.** Every bullet has a concrete value or `none`. `TBD`
  means "I don't actually know yet" → stop, read more, ask.
- **No "etc."** Enumerate or say `none`.
- **`Principles in play` is mandatory** (P1+P2). For anything
  beyond a typo it lists at least one P; for a non-trivial change
  it usually lists three or more. Empty list = stop, you have not
  classified the change.
- If `Files to MODIFY` exceeds 5 entries, the change is **not**
  small (P1) — re-classify per §5 and treat it as a multi-step
  change per `26-` §7.
- If `Files / layers explicitly NOT touched` is empty, the agent
  hasn't thought about scope (P1, P3) — refuse to start coding
  until that field has at least one concrete entry.
- If `Migration?` is `Yes` and there is no `deploy order` note,
  the plan violates P10 — fix it.
- If `Queue / worker contract impact?` is `Yes` without an
  idempotency note, the plan violates P7 — fix it.
- If `Security defaults touched?` is `Yes` and the justification
  cell weakens a default without an ADR, the plan violates P11 —
  refuse to proceed; surface to user.

A plan without these guarantees is a wishlist; a wishlist is what
gets reviewers to ask for a rewrite.

---

## 7. Change impact analysis

For any non-trivial change, mentally answer:

| Question | Why it matters |
|---|---|
| Which **modules** does the change directly modify? | scope baseline |
| Which **modules import** the modified ones? | indirect blast radius |
| Does the change cross a **layer boundary**? | architecture review needed |
| Does it touch the **queue contract** (`_job_id`, retries, status transitions)? | risk of duplicate or lost jobs |
| Does it touch **DB schema**? | requires migration + rollback plan |
| Does it change **temp link** semantics (TTL, max_downloads, token format)? | breaks in-flight links / audit |
| Does it change **storage layout** under `STORAGE_PATH`? | cleanup may miss files; nginx mount may misalign |
| Does it touch **deploy** (Dockerfile, compose, nginx, install)? | needs idempotency review + runbook update |
| Does it change **what is logged or how**? | observability regression |
| Does it change **what is exposed publicly** (port, header, route)? | security review required |
| Does it change **runtime config** (env var) or **secrets**? | per-stack `.env` + docs + install.sh |

If any answer is "yes" and you don't have the matching doc open,
**go back to §4**.

---

## 8. Layer-by-layer implementation rules

### 8.1 `app/bot/` — Telegram handlers, keyboards, callbacks

**Purpose:** receive Telegram updates, render UI, dispatch to use
cases.

**Allowed imports:** `telegram`, `telegram.ext`, `app/application/*`,
`app/utils/*`, `app/logging_config`, `app/exceptions`,
`app/bot/container`.

**Forbidden imports:** `app/infrastructure/*`, `app/workers/*`,
`app/api/*`, raw `redis`, `sqlalchemy`, `arq`.

**Typical changes:** new command, new button, new inline keyboard,
new error message, new callback parser.

**Typical mistakes:** doing DB lookups inside the handler;
synchronous `sleep`; storing big payloads in `callback_data`
(>64 bytes); skipping the three-tier exception pattern.

### 8.2 `app/application/` — use cases, services, ports

**Purpose:** orchestrate one user intent; coordinate domain +
infrastructure protocols. The application layer **defines** the
abstractions that infrastructure implements.

**Allowed imports:** `app/domain/*`, `app/utils/*`,
`app/exceptions`, stdlib, **protocols** for infra (defined in
`app/application/services/*`).

**Forbidden imports:** any concrete infrastructure, any Telegram /
HTTP / DB driver.

**Typical changes:** new use case, new service interface, new DTO,
new orchestration step.

**Typical mistakes:** importing `sqlalchemy` to "make this query
faster"; injecting a concrete `Redis` instead of a protocol;
returning ORM models from a use case.

### 8.3 `app/domain/` — entities, enums, repository protocols

**Purpose:** the unchanging language of the system.

**Allowed imports:** stdlib only.

**Forbidden imports:** anything else, period.

**Typical changes:** new entity field with a clear invariant; new
enum value (with migration!); new repository protocol method.

**Typical mistakes:** putting a SQLAlchemy `Column` here; importing
`Settings`; calling `datetime.utcnow()` inside an entity (let
callers pass time).

### 8.4 `app/infrastructure/` — concrete implementations

**Purpose:** speak to the outside world (Postgres, Redis, Telegram,
yt-dlp, ffmpeg, filesystem, HTTP).

**Allowed imports:** everything inward (`domain`, `application`),
plus third-party libs.

**Forbidden imports:** `app/bot/*`, `app/api/*`, `app/workers/*`.

**Typical changes:** new repository impl, new provider, new HTTP
client, new storage backend.

**Typical mistakes:** business logic creeping in (e.g. "if the file
is > 50MB, send via temp link" — that's a use case); leaking ORM
models out of `app/infrastructure/db/`; long-lived sessions.

### 8.5 `app/workers/` — arq runtime, scheduled jobs

**Purpose:** run background work (download jobs, cleanup, backups).

**Allowed imports:** `app/application/*` (use cases, services),
`app/infrastructure/*` only via `composition.build_worker(...)`.

**Forbidden imports:** any direct `app/bot/*` or `app/api/*`
(workers don't render UI or serve HTTP).

**Typical changes:** new arq task, new schedule, new retry
policy.

**Typical mistakes:** in-process state across jobs; non-deterministic
`_job_id`; swallowing exceptions (kills retries).

### 8.6 `app/api/` — FastAPI routes

**Purpose:** internal `/healthz`, `/readyz`; public `/d/{token}`.

**Allowed imports:** `app/application/*`, `app/utils/*`, FastAPI;
`app/infrastructure/*` only via `composition.build_api(...)`.

**Typical changes:** new health probe, new public route (rare).

**Typical mistakes:** streaming files via `FileResponse` (must use
`X-Accel-Redirect`); skipping `ensure_within` on a path; leaking
internal errors via response body.

### 8.7 `deploy/` and `deploy/scripts/`

**Purpose:** install, configure, deploy, back up, restore, clean
up the system on Ubuntu 24.04 LTS.

**Allowed:** bash, docker compose YAML, nginx conf, certbot, ufw,
crontab.

**Forbidden:** Python code; project Python imports; assumptions
about non-Ubuntu OSes; non-idempotent operations without confirmation
prompts.

**Typical changes:** new compose service, new env var passthrough,
new firewall rule, new install step, bumped image tag.

**Typical mistakes:** non-idempotent `apt install` (always
`apt install -y --no-install-recommends`); destructive `rm -rf`
without prompt; `chmod 777`; missing `set -Eeuo pipefail`.

### 8.8 `app/tests/` — unit + integration tests

**Purpose:** prove the contract; prevent regressions.

**Allowed:** `pytest`, `pytest-asyncio`, fakes for any infra
protocol, `monkeypatch`.

**Forbidden:** real network in unit tests; real DB/Redis in unit
tests (only `@pytest.mark.integration`); shared mutable state
across tests.

**Typical changes:** add a test for every code change, period.

**Typical mistakes:** `pytest.mark.skip` to make CI green;
asserting on log strings; using `time.sleep` in async tests.

### 8.9 `docs/`

**Purpose:** the spec. Kept in lockstep with code.

**Rule:** behaviour change → doc change in the **same PR**. Locked
decision change → ADR in the **same PR**.

---

## 9. Rules for schema changes

The single highest-risk area in the codebase. Read
[`12-db-schema.md`](12-db-schema.md) before touching anything.

### 9.1 When you need a migration

You need a migration any time you:
- add, remove, or rename a column;
- add, remove, or rename a table;
- add or remove an index or constraint;
- add a value to an existing PostgreSQL ENUM;
- change a column's type or default;
- change a unique-key composition.

You do **not** need a migration for ORM-only changes that don't
hit Postgres (e.g. adding a `__repr__` on a model class).

### 9.2 How to write a migration

1. `alembic revision --autogenerate -m "<short_msg>"`.
2. **Review the diff by hand.** Autogen misses ENUM additions and
   guesses wrong about renames.
3. Provide a `downgrade()` or document explicitly why it's empty.
4. For data backfill: write the SQL inside `upgrade()`, gated so
   it runs only when needed (`if conn.dialect.name == "postgresql"`).
5. Test locally: `alembic upgrade head` on a fresh DB, then
   `alembic downgrade -1 && alembic upgrade head`.

### 9.3 Avoiding destructive changes

- **Don't drop columns** in the same migration that stops writing
  to them. Two-step: stop writing → next release → drop.
- **Don't rename columns**; add new + dual-write + backfill + drop
  old over multiple releases.
- **Don't change a non-null default** without backfilling first.
- **Don't add a non-null column without a default** to an existing
  populated table — it will fail.

### 9.4 Rollback thinking

For every migration, ask: **what happens if we revert the code
without running `alembic downgrade`?** If the new code requires
the new schema and the old code can't tolerate it, you have a
deploy-order problem. The standard order is:
1. Apply migration (compatible with both old and new code).
2. Deploy new code.
3. (Later) clean up no-longer-needed schema in a follow-up migration.

### 9.5 What to update together

| You changed | You also update |
|---|---|
| ORM model (`app/infrastructure/db/models.py`) | alembic migration; relevant repository impl; doc `12-db-schema.md` |
| Domain entity (`app/domain/entities/*`) | use cases that touch it; tests; doc `04-domain-model.md` |
| Repository protocol (`app/domain/repositories/*`) | every implementation; every use case using it; tests |
| Enum (`app/domain/enums.py`) | ALTER TYPE migration; every `match`/branch on the enum |

---

## 10. Rules for provider changes

### 10.1 The provider contract

A provider implements `BaseProvider` (see
[`07-provider-architecture.md`](07-provider-architecture.md)):

- `async def get_info(url) -> MediaInfo`
- `def build_options(info) -> list[DownloadOption]` (pure)
- `async def download(url, option, *, target_dir) -> DownloadResult`

These three methods are the **public surface**. Application and
worker code only depend on this surface.

### 10.2 Changing an existing provider (YouTube / Instagram)

**Allowed:**
- Adjust `format_spec` strings.
- Add new buckets to `build_options` (e.g. add 1440p).
- Improve metadata extraction.
- Tighten error messages.

**Requires extra care:**
- Removing a `DownloadOption.key` — in-flight callbacks reference
  these keys; migration period needed.
- Changing the meaning of an existing option key (e.g. "audio_mp3"
  becoming AAC) — break the contract; either rename or version.

**Forbidden without an ADR:**
- Switching from `yt-dlp` to a different downloader.
- Calling `subprocess` directly instead of going through
  `YtDlpRunner` / `FFmpegRunner`.

### 10.3 Adding a new provider

Follow [`30-add-new-provider-guide.md`](30-add-new-provider-guide.md)
end-to-end. The short version:
1. New `Platform` enum value (+ alembic ENUM migration).
2. URL detection in `app/utils/url.py`.
3. `<Platform>Provider` extending `BaseProvider`.
4. Register in `_build_provider_registry(...)`.
5. Tests: URL detection + provider unit test with fake yt-dlp.
6. Docs: provider table in `07-provider-architecture.md`; entry in
   `30-add-new-provider-guide.md` worked examples.

### 10.4 What to test

- URL detection: 3+ representative URL shapes (long, short, mobile
  subdomain, share link).
- `get_info` against a recorded yt-dlp JSON fixture (no network).
- `build_options` against several `MediaInfo` shapes (single video,
  gallery, audio-only).
- Failure modes: empty `formats`, geo-block error, age-gated.

### 10.5 What to document

- Provider table row in `07-provider-architecture.md`.
- Worked example in `30-add-new-provider-guide.md` §10.
- Any cookies / auth requirements in
  `13-config-and-env.md`.

---

## 11. Rules for queue and worker changes

### 11.1 The hard contract

| Property | How it's enforced |
|---|---|
| **Idempotency** | `_job_id` is deterministic for a given logical request; arq dedupes |
| **Bounded retries** | `max_tries` set per task; exhausted retries → job marked `failed` |
| **No duplicate side effects on retry** | sender writes a "delivered" mark before exiting; subsequent retries skip if already delivered |
| **Stateless workers** | workers can be killed and replaced any time |
| **Partial-failure recovery** | scratch dirs cleaned by cleanup worker; DB row reflects `last error` |

### 11.2 Don'ts

- Don't change `_job_id` format (it's a wire-format contract; old
  in-flight jobs become orphans).
- Don't add an unbounded retry loop ("just keep retrying until it
  works").
- Don't catch `Exception` and continue inside a worker task —
  arq's retry / status reporting depends on exceptions reaching it.
- Don't block the event loop (no `time.sleep`, no sync IO).
- Don't store state on `self` between job invocations.

### 11.3 Adding a new task

1. New module `app/workers/tasks/<task>.py`.
2. Async function with the arq signature
   `async def <name>(ctx: dict, ...)`.
3. Register in `WorkerSettings.functions`.
4. Add a producer method to `app/application/services/queue.py` and
   implement it in `app/infrastructure/queue/producer.py`.
5. Set `max_tries`, `retry_delay`, `unique=True` if applicable.
6. Test the inner function with a fake `ctx`.

### 11.4 Handling partial failures

- If a download produces some files before failing: either commit
  them (mark job `partial_success` and notify the user with what
  succeeded) or roll them back (delete the scratch dir). Never
  leave dangling state.
- The cleanup worker is the safety net — but don't rely on it for
  correctness, only for resource hygiene.

---

## 12. Rules for deployment-related changes

### 12.1 When to touch which file

| You want to… | Edit |
|---|---|
| Add a new container service | `deploy/nl1/docker-compose.yml` or `deploy/nl2/docker-compose.yml` |
| Change container env | the relevant compose `environment:` block + `.env` template |
| Change a build step | the relevant `docker/<image>.Dockerfile` |
| Change Nginx routing | `deploy/nginx/conf.d/media.conf.template`, `deploy/nginx/nginx.conf`, or `deploy/nginx/snippets/*.conf` |
| Change Certbot behavior | `deploy/certbot/*` + `certbot_init.sh` |
| Change CI pipeline | `.github/workflows/<flow>.yml` |
| Add an install step | `deploy/scripts/install.sh` (idempotent!) |
| Add an operator script | `deploy/scripts/<name>.sh` (idempotent + prompts) |

### 12.2 Idempotency requirement

Every operator script must be safe to re-run. Patterns:

```bash
# Idempotent file write
install -m 0644 -D /dev/stdin "$TARGET" <<EOF
...
EOF

# Idempotent directory creation
mkdir -p "$DIR"

# Idempotent service install
if ! systemctl is-enabled --quiet "$SVC"; then
  systemctl enable --now "$SVC"
fi

# Idempotent apt
apt-get install -y --no-install-recommends "$PKG"
```

If a step is inherently destructive (`restore.sh`, dropping a
firewall rule), it must `confirm "..." "N"` before proceeding.

### 12.3 What to update together

| You changed | You also update |
|---|---|
| A Dockerfile | image-tag policy in `19-docker-architecture.md`; CI build matrix in `.github/workflows/build-images.yml` |
| A compose service | per-stack docs in `19-` and `20-`; healthcheck if applicable |
| Nginx config | doc `10-temp-links-and-delivery.md`; security headers per `17-security.md` |
| An install step | doc `20-deployment.md` walkthrough; runbook in `24-runbooks.md` if it's a recovery step |
| An env var | `.env.example` + per-stack `deploy/nl*/.env.example` + compose env block + `13-config-and-env.md` |

### 12.4 Verifying idempotency

Before merging a deploy script change:
1. `bash -n deploy/scripts/<file>.sh` (parse check).
2. `shellcheck --severity=warning deploy/scripts/<file>.sh`.
3. Re-run the script on a system where it has already been run.
4. Re-run after manually breaking one of the post-conditions
   (e.g. delete the file the script created); confirm the second
   run heals the system.

### 12.5 Host-side autodeploy canonical path

When the task is "install autodeploy", "run autodeploy now", or
"verify autodeploy on the servers", use this order and do not invent a
different flow:

| Step | Agent action | Why / verify |
|---|---|---|
| 1 | Read `20-deployment.md` and `21-cicd.md` before touching hosts. | `20-` is the operational playbook; `21-` explains the workflow gates and token scope. |
| 2 | Make repo changes first; push the target SHA; do not treat a live-host edit as source of truth. | The only host-local exception is `/etc/dwtgbot/autodeploy.env`. |
| 3 | Wait for `ci.yml` and `build-images.yml` to succeed for the exact SHA. | `deploy/scripts/auto_deploy.sh` gates on both. |
| 4 | For first-time setup, run `sudo bash deploy/scripts/install_autodeploy.sh nl1` / `nl2`, fill `/etc/dwtgbot/autodeploy.env`, and `systemctl enable --now dwtgbot-autodeploy.timer`. | Installation is one-time per host, not per deploy. |
| 5 | For an immediate rollout, start `dwtgbot-autodeploy.service` on NL-1, wait for success, then do the same on NL-2. | NL-2 is intentionally gated on `nl1-autodeploy` success for the same SHA. |
| 6 | Verify `git -C "$REPO_PATH" rev-parse HEAD`, `${AUTODEPLOY_STATE_DIR}/${DEPLOY_TARGET}.last_successful_sha`, timer=`active`, and service usually=`inactive`. | That is the real steady state after a successful oneshot run. |

Never:
- start from NL-2 first when you need a deterministic immediate rollout;
- edit `/etc/systemd/system/dwtgbot-autodeploy.*` by hand if the repo
  assets can simply be reinstalled;
- store or commit `GITHUB_TOKEN` anywhere except
  `/etc/dwtgbot/autodeploy.env`;
- conclude "deploy succeeded" from timer state alone; always verify the
  exact SHA.

---

## 13. Documentation obligations

### 13.1 What must be updated together

| If you change… | Update these docs |
|---|---|
| Architecture / layering | `02-architecture.md`, `03-project-structure.md`, possibly an ADR |
| Domain model | `04-domain-model.md` |
| Bot UX | `06-bot-flow.md` (handler catalogue, callback contract) |
| Provider behavior | `07-provider-architecture.md` |
| Download pipeline | `08-download-pipeline.md` |
| Queue / worker | `09-queue-and-workers.md` |
| Public delivery | `10-temp-links-and-delivery.md` |
| Storage | `11-storage-strategy.md` |
| DB | `12-db-schema.md` |
| Config / env var | `13-config-and-env.md` |
| Logging | `14-logging-observability.md` (event catalogue) |
| Healthchecks | `15-healthchecks.md` |
| Errors | `16-error-handling.md` (exception catalogue) |
| Security surface | `17-security.md` |
| Tests strategy | `18-testing-strategy.md` (rare) |
| Docker images | `19-docker-architecture.md` |
| Deploy procedure | `20-deployment.md` |
| CI/CD | `21-cicd.md` |
| Backup behavior | `22-backup-restore.md` |
| Cleanup behavior | `23-cleanup-retention.md` |
| New incident pattern | `24-runbooks.md` |
| Coding rule | `27-coding-standards.md` (rare) |
| Workflow / agent rule | this file (`25-`) and `26-cursor-rules.md` |
| New term used in 3+ docs | `33-glossary.md` |

### 13.2 ADR obligation

Open an ADR (`docs/adr/000N-<slug>.md`) when:
- Changing a locked decision.
- Picking between options with significant trade-offs.
- Introducing or removing a major dependency.
- Changing a public contract (callback data, temp link token,
  env var meaning).

### 13.3 README obligation

Update the root `README.md` only when:
- Adding/removing a top-level capability the user-facing summary
  describes.
- Changing the locked-decisions table.
- Changing how to set up locally (rare).

### 13.4 Язык документации

Все новые и изменяемые описания документации агенты ведут на русском
языке. Технические идентификаторы, имена команд, переменные окружения,
пути, API routes, event names и названия библиотек остаются в исходном
виде, чтобы не ломать ссылки, команды и поиск по коду.

---

## 14. Testing obligations

Seven mandatory test categories. For any change, ask: "which of
these does my change touch?" Add tests in **every** touched
category.

| Category | Lives in | When required |
|---|---|---|
| **Unit (pure functions, utils)** | `app/tests/test_<thing>.py` | every code change in `app/utils/`, `app/domain/` |
| **Domain entity tests** | `app/tests/test_<entity>.py` | when adding invariants / methods on entities |
| **Use case tests with fakes** | `app/tests/test_<use_case>.py` | every new / modified use case |
| **Codec / serializer round-trip tests** | `app/tests/test_callback_codec.py`, similar | callback data, token format, log formatting |
| **Provider tests with fake yt-dlp** | `app/tests/test_providers_<platform>.py` | every provider change |
| **Config validation tests** | `app/tests/test_config.py` | every new env var |
| **Integration (opt-in)** | `@pytest.mark.integration` | when behaviour can't be exercised by fakes |

Migration-related thinking (not all expressible as pytest):
- "Does the migration apply on a fresh DB?" — `alembic upgrade head`.
- "Does it apply on top of the previous head?" — fresh + previous.
- "Is downgrade safe?" — try it locally.

---

## 15. Logging and observability obligations

### 15.1 What to log

Log at the boundary between subsystems. The standard log moments:

| Moment | Event name template | Level |
|---|---|---|
| User action received | `bot_command`, `bot_callback` | INFO |
| Use case start / done | `<use_case>_started`, `<use_case>_done` | INFO |
| Use case failed (expected) | `<use_case>_failed` | WARNING |
| Use case failed (unexpected) | `<use_case>_unexpected_error` | ERROR (`logger.exception`) |
| External call done (provider, telegram, db) | `<dependency>_<verb>_done` | INFO |
| External call failed | `<dependency>_<verb>_failed` | WARNING / ERROR |
| Health/readiness state change | `health_state_changed` | INFO |

### 15.2 Correlation context

At the **top** of every async unit of work:

```python
with bind_context(request_id=req_id, job_id=job.id, user_id=uid, chat_id=cid):
    ...
```

If you're inside a task that doesn't have a request_id, generate
one and bind it. Without correlation, multi-service log tracing is
impossible.

### 15.3 What never to log

- Full URLs that may contain tokens / cookies.
- The `BOT_TOKEN`, DB password, Redis password, any secret.
- Authorization headers, cookies, session ids.
- Full temp-link tokens (last 4 chars only is fine for debug).
- Arbitrary user message bodies (might contain sensitive info).

### 15.4 When to add a new log event

Add one when:
- The event is what an on-call engineer would search for in logs.
- It marks a state transition.
- It marks a decision point ("I chose temp link because file > 50MB").

Do **not** add log events for:
- Loop iterations.
- Trivial branches.
- Successful happy-path internals (cover with metrics if needed).

Every new event name goes into the catalogue in
[`14-logging-observability.md`](14-logging-observability.md).

---

## 16. Security obligations

### 16.1 Always

- Validate every URL with `app/utils/url.py::detect(...)`.
- Pass every filename through `sanitize_filename(...)`.
- Pass every path through `ensure_within(STORAGE_PATH, ...)`.
- Use `asyncio.create_subprocess_exec(arg, arg)`, **never** shell
  strings.
- Use SQLAlchemy parameter binding; never f-string SQL.
- Read secrets only via `Settings`; never log them.

### 16.2 Never

- Open public ports on NL-1.
- Make Nginx storage `location` non-`internal`.
- Stream files directly via `FileResponse` for the public surface.
- Add a new `subprocess.run(..., shell=True)` call.
- Log any token, secret, cookie, or Authorization header.
- Embed credentials in code, configs, or docs.
- Loosen TLS / HSTS / firewall defaults without an ADR.

### 16.3 Adding a new public surface

If you add anything reachable from the Internet (new endpoint, new
header, new query param):

1. Add rate limiting (Nginx `limit_req_zone`).
2. Validate inputs at the boundary; trust nothing.
3. Document the change in [`17-security.md`](17-security.md).
4. Consider whether it changes the threat model — if yes, ADR.

---

## 17. Hard anti-patterns

A non-exhaustive list of things you must **not** do. Each entry
carries the violated principle code from §1.A.

### Code-level

- ❌ Putting business logic in `app/bot/handlers/*` — **P6**.
- ❌ Importing `app/infrastructure/*` from `app/domain/*` or
  `app/application/*` — **P5**.
- ❌ Reading `os.environ[...]` outside `app/config.py` — **P5**, **P11**.
- ❌ Using `subprocess.run(..., shell=True)` — **P11**.
- ❌ Bare `except:` or `except Exception: pass` — **P7** (kills retries).
- ❌ `time.sleep` in async code — **P7**.
- ❌ `print()` for logging — **P4** (event catalogue stays in sync).
- ❌ Catching `BaseException`.
- ❌ Mutable default arguments.
- ❌ Changing `_job_id` format because "it's clearer" — **P7**.
- ❌ Storing big payloads in Telegram `callback_data` — **P6**.

### Architecture-level

- ❌ Migrating Postgres to NL-2 — **P2**, ADR-0005.
- ❌ Opening 80/443 on NL-1 — **P11**, ADR-0005.
- ❌ Putting `yt-dlp` calls outside `YtDlpRunner` / providers — **P8**.
- ❌ Streaming public files via FastAPI directly (must use
  `X-Accel-Redirect`) — **P11**.
- ❌ Inventing a new top-level layer to fit "this one feature" — **P2**.

### Operational

- ❌ Non-idempotent install / deploy steps — **P9**.
- ❌ Destructive operations without a `confirm` prompt — **P9**.
- ❌ `chmod 777` — **P11**.
- ❌ `rm -rf` with an unbounded variable — **P11**, **P9**.
- ❌ Changing TLS / firewall / HSTS defaults without an ADR — **P11**.
- ❌ Adding env vars only to `.env.example` (forgetting compose
  passthrough) — **P4**.

### Documentation

- ❌ Creating a new top-level doc when an existing 00–33 fits — **P3**.
- ❌ Changing behaviour without updating the matching doc — **P4**.
- ❌ Changing a locked decision without an ADR — **P2**.
- ❌ Writing prose ADRs without "Consequences" — that section is
  the actual deliverable — **P2**.

### Cross-cutting (each violates ≥2 Ps)

- ❌ Bundling unrelated changes in one PR — **P1**, **P3**.
- ❌ "Refactor while you're here" — **P1**, **P3**.
- ❌ Schema change without migration + rollback note — **P10**, **P4**.
- ❌ Queue/worker change without idempotency note — **P7**, **P4**.
- ❌ Provider change that leaks platform specifics into application
  code — **P8**, **P5**.
- ❌ Security default loosened "for performance" without ADR — **P11**, **P2**.

### 17.A Mid-edit smell signals (halt and re-plan)

These are not anti-patterns in the diff — they are **behavioural
signals from your own session**. If any one becomes true mid-edit,
**stop, save no more files, and re-plan**. They are early-warning
indicators of a PR that will need to be rewritten. Each signal
maps to the principle (§1.A) it warns you about.

| Signal | Principle at risk | What it really means | Required next step |
|---|---|---|---|
| Opened a 4th file that wasn't in the §6.A plan | P1, P3 | scope drift starting | revert untracked changes; refine plan |
| Wrote `# TODO: handle X later` | P1, P4 | you don't actually understand X | stop; either handle X or surface it as a follow-up task |
| Typed `except Exception:` to make a test pass | P7, P11 | you're hiding the bug | revert; classify the error per `16-error-handling.md` |
| About to install a new dependency | P1, P2 | unapproved scope expansion | stop; surface to user before installing |
| Searching the codebase to learn "how it usually works" | P2 | you skipped §4 reading | go read the matching `docs/` topic now |
| Rewriting code that wasn't in the plan because "it's clearer" | P1, P3 | speculative refactor | revert; note as follow-up PR |
| Can't explain why a specific line you just added is needed | P1 | low-confidence change | revert; ask or document the assumption |
| Modifying `app/composition.py` for the second time | P2, P5 | DI surface is shifting under you | stop; you may have picked the wrong layer for the new code |
| Just copy-pasted a code block between files | P1, P5 | duplication forming | extract to the right module per §2.3 |
| Touching > 3 files with the change still labelled "small" | P1 | mis-classification | re-classify per §5 |
| Can't restate the goal in one sentence anymore | P1 | scope expanded silently | restate goal; trim diff back |
| Tempted to skip tests "this once" | P4 | quality regression starting | stop; tests are part of done (§3.8, §19.3) |
| About to add `subprocess.run(..., shell=True)` for "one quick command" | P11 | shell-injection risk | use `asyncio.create_subprocess_exec(arg, arg)` |
| About to commit a migration without `downgrade()` or a comment justifying it | P10 | irreversible schema change | add `downgrade()` or document why empty |
| Editing a deploy script and didn't re-run it on a clean system | P9 | idempotency unproven | re-run on a system where it has already been applied |
| About to log a full URL / token / secret | P11 | credential leak | strip secrets; log host + last-4 only |

Every entry above started life as a real PR that had to be redone.
The cost of pausing on a signal is seconds; the cost of ignoring
one is a redo.

---

## 18. Common mistakes by AI agents

Patterns observed in past PRs from various AI agents. Each row
names the principle (§1.A) it violates, so the cure is unambiguous.

| Mistake | Principle | Root cause | Cure |
|---|---|---|---|
| Over-refactoring | P1, P3 | "while I'm here" instinct | land focused change first; refactors as separate PRs |
| Layer leaks | P5 | grep-driven coding without reading `02-architecture.md` | always classify the layer before adding code |
| Hidden coupling via `from x import y` cycles, "fixed" with TYPE_CHECKING | P5 | shortcut around the real fix | fix the layering, not the import |
| Incomplete migrations (forgot ENUM ALTER, forgot index) | P10 | autogen trusted blindly | always review autogen by hand |
| No rollback thinking | P10 | "we'll never revert" optimism | every migration must answer "what if old code runs against new schema" |
| Missing docs updates | P4 | "code is the doc" fallacy | docs are part of the contract; same PR |
| Missing tests | P4 | "trivial change" rationalization | tests are cheap when they're written; expensive in regression |
| Wrong assumption about provider output | P8 | reading old yt-dlp docs | always inspect with `yt-dlp -j` or recorded fixture |
| Ignoring queue semantics | P7 | thinking of arq as "background async" | it's a distributed queue with retries — design for replay |
| Breaking install scripts | P9 | tested only the happy path | always run twice; always test recovery scenarios |
| Carelessly editing compose files | P9 | tidiness instinct | preserve YAML anchors, healthchecks, restart policies |
| Suggesting `black` / `requests` / sync code | P2 | training data drift | this codebase is async-first, ruff-formatted, no `black` |
| Bundling unrelated changes in one PR | P1, P3 | productivity bias | split into reviewable PRs |
| Adding `print()` for "easy debugging" | P4 | habit | `_logger.debug(...)` or remove |
| Silently dropping a feature flag | P3, P4 | "simplification" | feature flags have lifecycles; manage them, don't delete them |
| Returning ORM models from a use case | P5 | "they look like DTOs" fallacy | map to domain entity inside the repository impl |
| Editing a shipped migration to "fix it" | P10 | treating migrations as mutable | migrations are immutable after merge; write a new one |
| Caching `MediaInfo` in a process-local dict | P7 | trying to "be efficient" | workers are stateless (§3.4); cache in Redis with TTL or not at all |
| Adding `app/services/`, `app/helpers/`, `app/core/` | P2 | "natural Python project layout" | layers are fixed (§2.3, §5.3); use existing layers or open ADR |
| Using `time.sleep(N)` for "polling" in async code | P7 | sync-mode thinking | `await asyncio.sleep(N)`; consider whether polling is even needed |
| Putting a 200-byte payload in `callback_data` | P6 | "easier than a DB row" | persist in DB, reference by id; `callback_data` ≤ 64 bytes |
| Suggesting "we should switch to <other queue / other framework / other language>" | P2 | training data drift toward popular alternatives | locked by ADR-0005; require superseding ADR before discussing |
| "Fixing" a flaky test with `@pytest.mark.skip` | P4 | green CI bias | fix the flake or delete the test with a written justification |
| Adding `subprocess.run(..., shell=True)` "for one quick command" | P11 | habit from one-off scripts | `asyncio.create_subprocess_exec(arg, arg)`; never `shell=True` |
| Putting a DB query inside a Telegram handler | P6 | "it's just one query" | move to a use case; handler calls the use case |
| Streaming a public file via `FileResponse` | P11 | bypassing Nginx feels easier | always `X-Accel-Redirect` for the public surface |
| Loosening Nginx `internal;` on the storage location | P11 | "simpler URLs" | the token check is the security boundary — it stays |
| Adding a new env var only to `.env.example` | P4 | forgetting compose passthrough | also add to `app/config.py`, per-stack `.env.example`, compose env block, `13-` doc |
| Mixing yt-dlp specifics into a use case | P8 | "just a small detail" | move into the provider; application code stays platform-agnostic |

---

## 19. Definition of done

A change is **done** when **all** of the following are true. If
you can't tick a box, fix it before declaring done.

### 19.1 Architecture

- [ ] No layer-direction violations introduced.
- [ ] No locked decision violated (or: an ADR exists in this PR).
- [ ] `composition.py` updated if a new dependency was added.

### 19.2 Code quality

- [ ] `ruff format .` clean.
- [ ] `ruff check .` clean.
- [ ] `mypy app` clean (or only pre-existing failures, called out).
- [ ] `from __future__ import annotations` in every changed file.
- [ ] Modern typing (`X | None`, `list[...]`).
- [ ] Module-level `_logger = get_logger(__name__)`.

### 19.3 Tests

- [ ] `pytest -m "not integration"` green.
- [ ] At least one test added per public function added.
- [ ] Tests are hermetic (no network, no real DB).
- [ ] Integration tests (if any) marked `@pytest.mark.integration`.

### 19.4 Docs

- [ ] All docs in §13.1 affected by this change have been updated.
- [ ] If a new doc was created, the `docs/README.md` index is
      updated.
- [ ] If an ADR was created, the `docs/adr/README.md` index is
      updated.

### 19.5 Config & deploy

- [ ] If new env vars: present in `.env.example`, in the right
      compose stack, and in the docs.
- [ ] If new container service: `restart`, `healthcheck`,
      `logging`, `network` set.
- [ ] If new public route: rate limit + auth considered.
- [ ] If new background task: schedule + cleanup behavior considered.
- [ ] If install script touched: `bash -n` + `shellcheck` clean +
      idempotency verified.

### 19.6 Security

- [ ] Read [`17-security.md`](17-security.md) §5 again.
- [ ] No new public-network exposure of NL-1 services.
- [ ] No raw user input in shell, SQL, or path.
- [ ] No secrets / tokens / cookies in logs.

### 19.7 Diff hygiene

- [ ] No unrelated files modified.
- [ ] No formatting churn outside the touched code.
- [ ] No dead code / commented-out blocks left behind.
- [ ] No new TODOs without an owner.

### 19.8 PR description

- [ ] Restates the goal.
- [ ] Lists the touched files.
- [ ] Lists the migrations added.
- [ ] Lists the docs updated.
- [ ] States the risk and rollback.
- [ ] Includes a test plan (manual + automated).

### 19.9 Principles compliance (P1–P11 gate)

The change is **not done** until every P from §1.A is either
honoured or has a documented exception (with reviewer sign-off and,
if applicable, an ADR).

- [ ] **P1 — Minimal safe scope.** Diff equals the §6.A plan; no
      drive-by files; "Files NOT touched" still accurate.
- [ ] **P2 — Architecture first.** Layer placement matches §2.3 +
      §8; ADR-0005 §2 untouched (or: superseding ADR in this PR).
- [ ] **P3 — No unrelated changes.** No formatting churn outside
      changed code; no renames you weren't asked for; no
      reordered imports for fun.
- [ ] **P4 — Docs / tests / config travel together.** Every doc in
      §13.1 affected by this change is updated; tests added/
      updated; env-var-mapping checklist (§19.5) green.
- [ ] **P5 — No infra leakage into domain.** Domain still imports
      stdlib only; application still talks to protocols.
- [ ] **P6 — No business logic in handlers.** Bot/API/worker
      handlers route to a single use case and do nothing else.
- [ ] **P7 — Queue semantics preserved.** `_job_id` formula
      unchanged; `max_tries` finite; retry idempotent; status
      transitions logged; no in-process worker state.
- [ ] **P8 — Provider contracts explicit.** Provider surface
      (`get_info`, `build_options`, `download`) unchanged or
      versioned; no platform code outside providers.
- [ ] **P9 — Deploy idempotency.** Every script change re-run on
      a healthy system; destructive ops gated by `confirm`;
      `bash -n` + `shellcheck` clean.
- [ ] **P10 — DB migration reasoning.** Schema change has Alembic
      migration with `downgrade()` (or justified empty); deploy
      order documented; migration not editing prior file.
- [ ] **P11 — Security defaults preserved.** No public port
      added on NL-1; Nginx `internal;` intact; `ensure_within` and
      `sanitize_filename` applied to every input-derived path/
      filename; no secret/token/cookie in logs.

If any box can't be ticked, the PR description must enumerate
which P is not honoured, **why**, and (for P2/P11) link the
superseding ADR. Silent exceptions are forbidden.

---

## 20. Escalation rules

You **must not proceed blindly** when any of the following is true.
Stop, surface the situation to the user, and propose options.

### 20.1 Stop and ask

- The request is ambiguous in a way that has architectural
  consequences ("add caching" — Redis? in-memory? per-process?).
- The request would touch a locked decision.
- The request requires a new top-level dependency.
- The request would change a public contract (env var meaning,
  callback format, token format).
- The request would touch security-sensitive code (`ensure_within`,
  `temp_link_service`, public API) and you're not confident.
- The implementation requires data backfill or downtime planning.

### 20.2 Mark uncertainty explicitly

Use language like:

> "Я не уверен, должно ли это поведение жить в use case или в
> infrastructure. Прошу решения. Варианты: (A) ..., (B) ...,
> trade-offs: ..."

Or in a PR description:

> "Open question: behavior under retry when the file is partially
> uploaded. Current PR assumes (A); alternative (B) requires a
> schema column. Marking this for reviewer decision."

### 20.3 Propose multiple options

When the right path isn't obvious:

1. List 2–3 options.
2. State the trade-offs (cost, risk, blast radius, future
   flexibility).
3. **Recommend** one — but make the recommendation falsifiable.
4. Wait for a decision before implementing.

### 20.4 Suggest an ADR

When the decision is non-trivial and likely to be revisited,
suggest opening an ADR:

> "This decision (e.g., move temp links to S3) will affect
> `temp_link_service`, the cleanup worker, and the security model.
> I recommend an ADR before implementation. Draft attached."

### 20.5 Refuse to invent architecture

If you encounter a situation where there is no documented pattern
and no obvious local analog, **do not invent one**. Surface the
gap. The user / reviewer will either point you to a hidden pattern,
write an ADR, or accept a one-off.

Inventing architecture silently is the single most damaging
mistake an agent can make.

### 20.6 Refusal templates (verbatim — pick one and fill `<…>`)

When a request triggers §4.4 red flags or hits an authority limit,
the agent must respond with one of the templates below **before**
producing a plan or any code. Use the closest match. The full set
(with full RU/EN wording) lives in
[`26-cursor-rules.md`](26-cursor-rules.md) §17.9; below is the
short index agents can navigate during a conversation.

| Situation | Template id (in `26-` §17.9) | Defends | Headline of the response |
|---|---|---|---|
| Request touches a row in ADR-0005 §2 | A | P2 (often P11) | "This change touches a locked row; I need a superseding ADR before any code" |
| Drive-by / scope-creep ask added on top of original request | B | P1, P3 | "The extra ask is out of scope for this PR; I will surface it as a follow-up" |
| Required reading is missing or you can't find a referenced file | C | P2 | "I'm missing context to answer; here is exactly what I need" |
| Request is ambiguous and the interpretation has architectural consequences | D | P2, P1 | "Three valid readings; please choose one before I plan" |
| Request asks for an action outside agent authority (push to main, prod ops, destructive ops) | E | P9, P11 | "Out of my authority; here is the exact command for you to run manually" |
| Request implies storing a secret in code/repo | F | P11 | "Hard refuse; correct path is `Settings` + `.env` + compose; I'll do the wiring, you place the value" |
| Request says "skip the tests" / "skip the docs" | G | P4 | "Tests and docs travel with code; I will not bypass §3.8 without a tracked debt and an explicit acknowledgement" |

Behaviour around refusals:

- A refusal is **not** a polite stall — it is a structured response
  ending with a concrete next action for the user.
- Never refuse without naming the rule (e.g. "ADR-0005 §3.6",
  "§4.5 drive-by edit ban") that produced the refusal.
- After a refusal, do not silently proceed with a "lite" version
  of the request. Wait for the user's reply.

---

## 21. Appendix — one-screen quick reference

| Question | Answer | Principle |
|---|---|---|
| Where do I start reading? | §4.1 universal floor | P2 |
| Did the user request smell wrong? | §4.4 red-flag table | P1, P2, P3 |
| Is this change locked? | check §3.9 + ADR-0005 §2 | P2 |
| Where does this code go? | §2.3 layers + §8 layer rules | P5, P6 |
| What plan must I emit? | §6.A verbatim plan template | P1, P2 |
| Do I need a migration? | §9.1 | P10 |
| Do I need an ADR? | §13.2 | P2 |
| Do I need to update docs? | §13.1 | P4 |
| Do I need new tests? | §14 (seven categories) | P4 |
| Did I break the queue contract? | §11.1 | P7 |
| Did I touch the provider surface? | §10.1 | P8 |
| Did the deploy script stay idempotent? | §12.4 | P9 |
| Did I introduce a security regression? | §16 | P11 |
| Should I stop mid-edit? | §17.A mid-edit smell signals | P1–P11 |
| Should I refuse the request? | §20.6 refusal templates | P1–P11 |
| Am I done? | §19 (full checklist + §19.9 P1–P11 gate) | P1–P11 |
| Should I stop and ask? | §20 | P2 |

**The eleven principles (§1.A) — keep them visible at all times:**

| | |
|---|---|
| **P1** Minimal safe scope | **P7** Queue semantics preserved |
| **P2** Architecture first | **P8** Provider contracts explicit |
| **P3** No unrelated changes | **P9** Deploy idempotency reasoned |
| **P4** Docs/tests/config together | **P10** DB migration reasoned |
| **P5** No infra leakage into domain | **P11** Security defaults intact |
| **P6** No business logic in handlers | |

**The five rules to never break (the principles in shorthand):**

1. **Read before you write (P2).** §4 is non-negotiable.
2. **Respect locked decisions (P2).** ADR-0005 §2 — twelve rows,
   no exceptions without a superseding ADR.
3. **Stay inside your layer (P5, P6).** §8 + `composition.py` is
   the only wiring point.
4. **Emit the plan before the diff (P1).** §6.A template, no
   `TBD`, no `etc.`, with explicit "files NOT touched" and
   "Principles in play".
5. **Docs / tests / config travel with code (P4).** §13.1 mapping
   is the contract.

When in doubt: stop, read, ask. The cost of asking is one
roundtrip; the cost of guessing wrong is one PR rewritten.
