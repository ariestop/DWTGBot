# DWTGBot — Documentation Index

This is the **single source of truth** for the DWTGBot architecture, conventions
and operations. The root [`README.md`](../README.md) is a marketing/overview
page; everything technical lives here.

> **For AI agents (Cursor, etc.):** before making any change, read at minimum
> [`00-overview.md`](00-overview.md), [`02-architecture.md`](02-architecture.md),
> [`03-project-structure.md`](03-project-structure.md), and
> [`25-agent-guide.md`](25-agent-guide.md). Then read the topic-specific doc(s)
> for the area you are touching. Cursor-specific rules are mirrored in
> [`.cursor/rules/`](../.cursor/rules/) and explained in
> [`26-cursor-rules.md`](26-cursor-rules.md).

---

## Reading order

### 1. New here? Start with foundation
- [`00-overview.md`](00-overview.md) — what the system is, who it serves, what it does not do
- [`01-product-purpose.md`](01-product-purpose.md) — user journey, product scope
- [`02-architecture.md`](02-architecture.md) — layers + diagrams (containers, sequence, data flow)
- [`03-project-structure.md`](03-project-structure.md) — folder map + dependency rules

### 2. How each subsystem actually works
- [`04-domain-model.md`](04-domain-model.md) — entities, invariants, state machines
- [`05-data-flow.md`](05-data-flow.md) — end-to-end happy + failure paths
- [`06-bot-flow.md`](06-bot-flow.md) — Telegram UX, callbacks, keyboards
- [`07-provider-architecture.md`](07-provider-architecture.md) — provider pattern
- [`08-download-pipeline.md`](08-download-pipeline.md) — yt-dlp + ffmpeg + safety
- [`09-queue-and-workers.md`](09-queue-and-workers.md) — arq lifecycle, retries
- [`10-temp-links-and-delivery.md`](10-temp-links-and-delivery.md) — X-Accel-Redirect
- [`11-storage-strategy.md`](11-storage-strategy.md) — paths, traversal, GC
- [`12-db-schema.md`](12-db-schema.md) — tables, indexes, migrations

### 3. Cross-cutting concerns
- [`13-config-and-env.md`](13-config-and-env.md) — env reference + validation
- [`14-logging-observability.md`](14-logging-observability.md) — structlog + correlation IDs
- [`15-healthchecks.md`](15-healthchecks.md) — liveness vs readiness
- [`16-error-handling.md`](16-error-handling.md) — `AppError` hierarchy
- [`17-security.md`](17-security.md) — threat model, hardening, secrets
- [`18-testing-strategy.md`](18-testing-strategy.md) — pyramid, fakes, integration

### 4. Operations
- [`19-docker-architecture.md`](19-docker-architecture.md) — multi-stage, non-root
- [`20-deployment.md`](20-deployment.md) — установка `single` (один хост) и `split` (NL-1 + NL-2)
- [`21-cicd.md`](21-cicd.md) — GitHub Actions
- [`22-backup-restore.md`](22-backup-restore.md) — pg_dump, retention, DR
- [`23-cleanup-retention.md`](23-cleanup-retention.md) — temp links, files, cache
- [`24-runbooks.md`](24-runbooks.md) — incident playbooks

### 5. Contributor & AI-agent guidelines
- [`25-agent-guide.md`](25-agent-guide.md) — AI agent operating manual (mental model, layered rules, change protocol, escalation)
- [`26-cursor-rules.md`](26-cursor-rules.md) — Cursor operational protocol (strict per-step rules, anti-patterns, final checklist; `.cursor/rules/*.mdc` inventory in Appendix B)
- [`27-coding-standards.md`](27-coding-standards.md) — style + lint rationale
- [`28-implementation-playbook.md`](28-implementation-playbook.md) — recipes
- [`29-feature-development-guide.md`](29-feature-development-guide.md) — branch → PR → release
- [`30-add-new-provider-guide.md`](30-add-new-provider-guide.md) — extending the bot

### 6. Reference
- [`31-troubleshooting.md`](31-troubleshooting.md) — symptom → cause → fix
- [`32-roadmap-and-extension-points.md`](32-roadmap-and-extension-points.md) — what's planned
- [`33-glossary.md`](33-glossary.md) — terms (NL-1, temp link, request_id, …)

### 7. Production hardening (operational targets)
- [`34-data-retention-and-privacy.md`](34-data-retention-and-privacy.md) — what we store, for how long, GDPR/DSR runbook, backup-purge strategies
- [`35-metrics-and-slo.md`](35-metrics-and-slo.md) — SLI/SLO catalogue, LogQL/PromQL recipes, multi-window burn-rate alerts, error-budget policy
- [`36-rate-limiting.md`](36-rate-limiting.md) — per-user/chat/global/per-domain quotas, fail-open Redis limiter, in-process Prometheus exporter (gated by `RL_ENABLED` / `METRICS_ENABLED`; see [ADR-0006](adr/0006-in-process-rate-limit-metrics-exporter.md))
- [`37-load-and-capacity.md`](37-load-and-capacity.md) — capacity equations, load-test recipes, scaling-up runbook (vertical → ADR-grade horizontal)

### 8. Architectural decisions
- [`adr/`](adr/README.md) — Architectural Decision Records (process + index)
  - [ADR-0001 — Two-server topology](adr/0001-two-server-topology.md) (amended by ADR-0011)
  - [ADR-0002 — `python-telegram-bot`](adr/0002-python-telegram-bot.md)
  - [ADR-0003 — `ruff format` (no `black`)](adr/0003-ruff-format-no-black.md)
  - [ADR-0004 — Temp links via Nginx `X-Accel-Redirect`](adr/0004-temp-links-via-nginx-x-accel.md)
  - 🔒 [**ADR-0005 — Locked architectural assumptions** (canonical 12-row register)](adr/0005-locked-architectural-assumptions.md)
  - [ADR-0006 — In-process Prometheus exporter for rate-limit metrics](adr/0006-in-process-rate-limit-metrics-exporter.md)
  - [ADR-0011 — Single-server topology (`single` рядом со `split`)](adr/0011-single-server-topology.md)

---

## Quick lookup by task

| I want to… | Read this |
|---|---|
| Onboard | `00`, `01`, `02`, `03` |
| Add a new bot command | `06`, `28` |
| Add a new media platform | `07`, `30` |
| Change DB schema | `12`, `28` |
| Touch the worker | `09`, `08`, `28` |
| Change delivery (Telegram vs link) | `10` |
| Modify temp-link / Nginx setup | `10`, `19`, `20` |
| Debug a production incident | `24`, `31` |
| Bump yt-dlp / ffmpeg | `08`, `21`, `24` |
| Add an env var | `13`, `27` |
| Write a migration | `12`, `28` |
| Add tests | `18`, `27` |
| Tweak CI | `21` |
| Roll out a deploy | `20`, `21` |
| Install / verify host-side autodeploy | `20`, `21`, `24`, `25` |
| Handle a "delete my data" / GDPR request | `34`, `17` |
| Define / change an SLO target | `35`, `adr/` |
| Tune the bot's rate limits | `36`, `13` |
| Plan a capacity / scaling change | `37`, `02`, `09`, `11` |
| Выбрать топологию / перейти с `single` на `split` | `adr/0011`, `20`, `24` §26, `37` §8.0 |
| As an AI agent — make any change | `25`, `26`, `27`, then topic-specific |

---

## Documentation conventions

- **Source of truth precedence (most → least authoritative):**
  1. ADR documents (`adr/*.md`) — hard architectural locks
  2. Topic docs (`02-…` to `33-…`)
  3. Inline code comments
  4. Root `README.md` (marketing summary)
- **Mermaid** is used for all diagrams. They render natively on GitHub.
- **Code references** use the project's relative paths (e.g.
  `app/application/use_cases/process_download.py`), never fragile line numbers.
- **Tables > prose** when comparing options or listing rules.
- **Anti-patterns and "do not"** sections appear at the end of each topic
  doc; AI agents must scan them before changes.
- **Locked decisions** are flagged with `> 🔒 LOCKED:` admonitions and
  back-link to the relevant ADR. The full canonical register is
  [**ADR-0005**](adr/0005-locked-architectural-assumptions.md) — start
  there before proposing any change to the foundation.
- **Status tags**:
  - `Status: Stable` — safe to rely on
  - `Status: Evolving` — interface may change with notice
  - `Status: Draft` — under review, do not depend on it yet

---

## How to update documentation

1. Find the right file(s) using "Quick lookup by task" above. **Never** create
   a new top-level doc unless none of 00–37 fits — instead, extend an existing one.
2. Preserve the document's structure (sections, headings) so cross-links stay valid.
3. If you change behaviour described in a doc, update the doc in the **same PR**.
4. If you make an architectural decision, add a new ADR (`adr/000N-…md`) and
   link to it from the relevant topic doc(s).
5. Run [`28-implementation-playbook.md`](28-implementation-playbook.md) checklists
   before merging.

---

## Contact / maintainers

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for contribution rules and
security disclosure.
