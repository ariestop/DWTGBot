# 29 — Feature development guide

> Status: Stable
> Audience: every engineer or AI agent designing or building a feature in DWTGBot.
> Companion docs:
> [`25-agent-guide.md`](25-agent-guide.md) — operating manual (how to think),
> [`26-cursor-rules.md`](26-cursor-rules.md) — operational protocol for AI tools,
> [`27-coding-standards.md`](27-coding-standards.md) — code-level rules,
> [`28-implementation-playbook.md`](28-implementation-playbook.md) — change-class playbooks (how to execute),
> [`adr/0005-locked-architectural-assumptions.md`](adr/0005-locked-architectural-assumptions.md) — locked rows.

> ⚠️ **Read this BEFORE you start designing a new feature in DWTGBot.**
>
> `28-` answers: *"I have a change of class X — what's the procedure?"*
> **`29-` answers: *"I have a feature idea — how do I take it from idea to a deployed, observable, rollback-safe surface without breaking architecture?"***
>
> The eleven principles **P1–P11** referenced throughout are defined
> canonically in [`25-` §1.A](25-agent-guide.md#1a-mandatory-principles-p1p11--canonical-register)
> and [`26-` §1.A](26-cursor-rules.md#1a-mandatory-principles-p1p11--operational-register).

---

## 1. Purpose of this guide

### 1.1 What this document is

A **product-aware operating manual** for building features in
DWTGBot. It complements `28-` (which is task-class-shaped) with a
feature-shape: idea → scope → design → code → tests → docs → deploy →
rollout → validation, with explicit checkpoints at each step.

### 1.2 What this document is NOT

- It is **not** a re-statement of `28-implementation-playbook.md` —
  for the *how* of any change-class, this guide cross-references
  there.
- It is **not** a UX style guide — Telegram UX patterns live in
  [`06-bot-flow.md`](06-bot-flow.md).
- It is **not** an ADR repository — architectural decisions live in
  `docs/adr/`.

### 1.3 Why a feature guide is needed *in this project specifically*

Features have a way of accidentally:

- **bleeding business logic into Telegram handlers** (P6 violation);
- **smuggling DB schema changes into "small" PRs** (P10 violation);
- **silently changing queue semantics** because the worker happens
  to be in the diff (P7 violation);
- **shipping without a docs / tests / config update** (P4 violation);
- **getting deployed without a rollout note** (P9 violation);
- **introducing new public surfaces without rate limiting** (P11
  violation).

Every one of these mistakes is **cheap to prevent at the design
phase** and expensive to fix after. This guide front-loads the
discipline.

### 1.4 Who this is for

| Reader | What this document does for them |
|---|---|
| **Engineer** | A pre-flight checklist and design template before opening files |
| **Tech lead / reviewer** | A reference for "what should a feature PR look like?" |
| **AI agent (Cursor, etc.)** | A scripted design phase + per-feature-type playbook + verbatim plan template |
| **Product-minded contributor** | A way to translate user need → architecturally-safe implementation |
| **First-day joiner** | A way to ship a real feature in week 1 without breaking anything |

---

## 2. Feature development principles

These ten principles are how features stay aligned with P1–P11.
Each is a working translation of one or more principles, framed for
feature work.

| # | Principle | Translation for feature work | Maps to |
|---|---|---|---|
| 1 | **Feature first, not random refactor** | A feature PR delivers a user-visible capability. If you find yourself "improving" unrelated code, stop and split. | P1, P3 |
| 2 | **Architecture first** | Decide layer + ADR-0005 impact **before** writing code. The structure is the contract. | P2 |
| 3 | **Minimal safe scope** | Smallest design that fully solves the user's stated need; no extras. | P1 |
| 4 | **User flow clarity** | If you can't draw the user flow on one screen, the feature is under-designed. | P2, P6 |
| 5 | **Explicit boundaries** | Every layer crossing is intentional and documented in the design note. | P5, P6 |
| 6 | **Testability built-in** | The feature is shaped so each layer is unit-testable with fakes; no test "added later". | P4 |
| 7 | **Observability by default** | New code paths an on-call engineer would care about have log events from day one. | P4 |
| 8 | **Config discipline** | New env vars are named, defaulted, validated, propagated to all five places (§15.2 of `28-`). | P4 |
| 9 | **Deployment awareness** | The feature's deploy story is part of the design, not an afterthought. | P9, P10 |
| 10 | **Documentation completeness** | Every doc the feature affects is updated **in the same PR**. No "docs-later" tickets. | P4 |

**Cross-cutting reminders** (lifted from P1–P11):

- Domain stays framework-free (P5).
- Handlers route, never decide (P6).
- Provider contracts are sacred (P8).
- Queue contracts are sacred (P7).
- Schema changes always migrate, with a deploy-order story (P10).
- Security defaults are floors (P11).

If you can articulate, for any feature, **which layers it touches,
which contracts it preserves, and what it intentionally does NOT
do**, you are working in the guide spirit.

---

## 3. Feature lifecycle

Every non-trivial feature passes through these eleven phases. The
lifecycle is the master script the rest of the document fills in.

```mermaid
flowchart TD
    A[1. Idea / request] --> B[2. Scoping]
    B --> C[3. Architecture fit]
    C --> D{ADR-0005 row touched?}
    D -- yes --> Z[STOP — draft superseding ADR]
    D -- no --> E[4. UX design]
    E --> F[5. Implementation design<br/>fill §7 mini-design note]
    F --> G[6. Coding<br/>per 28- playbook for the class]
    G --> H[7. Tests added<br/>P4]
    H --> I[8. Docs updated<br/>P4]
    I --> J[9. Deploy readiness<br/>P9, P10]
    J --> K[10. Rollout<br/>migration first, code second if needed]
    K --> L[11. Post-deploy validation<br/>logs, healthchecks, manual smoke]
```

### 3.1 Phase definitions

| # | Phase | Done when… |
|---|---|---|
| 1 | **Idea / request** | The user need is captured in one sentence; the user-visible behaviour change is articulated |
| 2 | **Scoping** | Feature class identified (§4); in-scope / out-of-scope explicit |
| 3 | **Architecture fit** | ADR-0005 §2 audit done; layers identified; contracts (queue / provider / DB / token) reviewed |
| 4 | **UX design** | If user-facing: state diagram or message catalogue exists; error paths designed |
| 5 | **Implementation design** | §7 mini-design note filled; reviewed (informally if solo, formally for non-trivial) |
| 6 | **Coding** | Per the corresponding `28-` playbook; inside-out order |
| 7 | **Tests added** | Per §17; coverage matches the class's mandatory tests |
| 8 | **Docs updated** | Per §18; same PR (P4) |
| 9 | **Deploy readiness** | Per §19; migration / compose / nginx / scripts changes carry deploy-order notes |
| 10 | **Rollout** | Per §23; migration first if needed; flag-gated if risky |
| 11 | **Post-deploy validation** | Logs show the new event names; healthchecks pass; manual smoke succeeds |

Skipping any phase costs more than running it.

---

## 4. Feature classification

Every feature has a **class**. The class drives the mandatory
checks, tests, and docs. This table is the feature-shaped sister of
`28-` §4 (which classifies *changes*, not features).

| Class | Examples | Layers usually touched | Key risks | Required tests | Docs to update |
|---|---|---|---|---|---|
| **F-Cmd** Bot UX feature / new command | `/about`, `/help`, new button | `app/bot/`, possibly `app/application/` | UX inconsistency; placement of logic in wrong layer | handler test + (if use case) use-case test | [`06-bot-flow.md`](06-bot-flow.md), [`16-error-handling.md`](16-error-handling.md) if new exception |
| **F-Flow** New callback-driven flow | "Choose quality → confirm → receive" | `app/bot/`, `app/application/`, possibly state store | State leakage across users; lost state on bot restart; oversized `callback_data` | one test per state transition + codec round-trip | [`06-bot-flow.md`](06-bot-flow.md) (flow diagram), [`14-`](14-logging-observability.md) (events) |
| **F-Prov** Provider capability | New format spec; better metadata; new bucket | `app/infrastructure/providers/<platform>/`, fixtures | Breaking `BaseProvider` contract; trusting yt-dlp output blindly | provider unit tests against recorded fixtures | [`07-provider-architecture.md`](07-provider-architecture.md) |
| **F-NewProv** New provider | TikTok, SoundCloud | `app/infrastructure/providers/<new>/`, `app/utils/url.py`, `app/composition/`, ENUM migration | URL detection priority; ENUM migration; auth | URL detection ≥3 shapes, get_info, build_options, failure modes | [`07-`](07-provider-architecture.md), [`30-add-new-provider-guide.md`](30-add-new-provider-guide.md), [`12-`](12-db-schema.md) (ENUM), [`13-`](13-config-and-env.md) if auth |
| **F-Queue** Queue/worker feature | New task; status field; retry policy | `app/workers/`, `app/application/services/queue.py`, `app/infrastructure/queue/` | `_job_id` semantics; idempotency; bounded retries | inner-function test with fake `ctx`; idempotent retry test | [`09-queue-and-workers.md`](09-queue-and-workers.md), [`14-`](14-logging-observability.md), [`24-`](24-runbooks.md) if new failure mode |
| **F-Deliv** Delivery / temp-link feature | TTL change; expiry warning; new header | `app/application/services/delivery.py`, `app/api/public/`, `deploy/nginx/` | `internal;` preserved; `X-Accel-Redirect` flow intact; rate limit | service test; public-route test; `nginx -t` | [`10-temp-links-and-delivery.md`](10-temp-links-and-delivery.md), [`17-security.md`](17-security.md) |
| **F-Admin** Admin / ops feature | Admin command; manual job control | `app/bot/`, `app/application/`, possibly DB row | Auth bypass; destructive ops without confirm | handler test + admin auth test | [`06-`](06-bot-flow.md), [`17-security.md`](17-security.md), [`24-`](24-runbooks.md) |
| **F-Obs** Observability feature | New log event; new metric; new probe | code in any layer, `14-`, `15-` | Missing correlation context; secrets in logs | structured-field round-trip test | [`14-logging-observability.md`](14-logging-observability.md), [`15-healthchecks.md`](15-healthchecks.md) |
| **F-Sec** Security feature | New rate limit; sanitization tightening | `app/api/`, `app/utils/`, `deploy/nginx/` | Threat model regression; over-tightening (legitimate UX broken) | tightening test (refused input); existing UX still works | [`17-security.md`](17-security.md); ADR if surface changes |
| **F-Cfg** Config-driven feature | Env var toggles a behaviour | `app/config.py`, all five env-template places | Default chosen wrong; var only added in one place | `Settings` validation test | [`13-config-and-env.md`](13-config-and-env.md); install script if secret |

### 4.1 Decision tree: "what class is my feature?"

```mermaid
flowchart TD
    A[Feature idea]
    A --> B{User-facing in Telegram?}
    B -- "no, ops only" --> Q1{Touches admin / ops surfaces?}
    Q1 -- yes --> CADM[F-Admin]
    Q1 -- no --> Q2{Adds log / metric / probe?}
    Q2 -- yes --> COBS[F-Obs]
    Q2 -- no --> Q3{Tightens security boundary?}
    Q3 -- yes --> CSEC[F-Sec]
    Q3 -- no --> CCFG[F-Cfg]
    B -- yes --> Q4{New platform supported?}
    Q4 -- yes --> CNP[F-NewProv]
    Q4 -- no --> Q5{Tweaks existing provider behaviour?}
    Q5 -- yes --> CPRV[F-Prov]
    Q5 -- no --> Q6{Changes how files are delivered?}
    Q6 -- yes --> CDLV[F-Deliv]
    Q6 -- no --> Q7{Changes background-job behaviour?}
    Q7 -- yes --> CQUE[F-Queue]
    Q7 -- no --> Q8{Multi-step interaction with state?}
    Q8 -- yes --> CFLW[F-Flow]
    Q8 -- no --> CCMD[F-Cmd]
```

**Multi-class features** (e.g., new provider + new bot command +
new env var) are decomposed into multiple PRs in dependency order
(see §23).

---

## 5. Pre-implementation checklist

This is the **gate before any file edit**. Every question must
have a concrete answer (not "TBD", not "we'll see").

### 5.1 The ten-question gate

- [ ] **What problem is solved?** One sentence, user-visible.
- [ ] **Who uses this?** End user, admin, ops, internal — pick one.
- [ ] **What is the expected user flow?** Drawable on one screen
      (or "no UI — internal").
- [ ] **What feature class (§4)?** F-Cmd, F-Flow, F-Prov, …
- [ ] **What layers are affected?** List them (`bot`, `application`,
      `infrastructure`, `worker`, `api`, `deploy`, `docs`).
- [ ] **Is a DB schema change needed?** If yes, design the migration
      shape (additive vs two-step).
- [ ] **Is queue / worker impact expected?** If yes, what changes
      about `_job_id`, retries, status transitions?
- [ ] **Is provider impact expected?** If yes, does `BaseProvider`
      surface stay unchanged?
- [ ] **Is deploy / config impact expected?** If yes, list compose /
      nginx / install / env changes.
- [ ] **What needs logging?** Pick events; bind correlation
      context.
- [ ] **Which docs need updating?** Per [§18 / `28-` §22](#18-documentation-requirements-for-new-features).
- [ ] **Can the scope be smaller?** If yes, do that.

If even one box is unchecked, **do not start coding**. Do the
design note first (§7).

### 5.2 Anti-patterns at the gate

| Symptom at the gate | Likely violation | Cure |
|---|---|---|
| User flow is "obvious, no need to draw it" | P2, P6 | Draw it; reveals 30% of the design |
| "We'll know what to log when we get there" | P4 | Pick events now; add to `14-` catalogue |
| "Migration is trivial — autogen will handle it" | P10 | Migration shape decided **at design**, not at code |
| "Don't need a flag, it's always-on" | P9 | Flag-gate anything risky for the first rollout |
| "Docs can come in a follow-up" | P3, P4 | Same PR, no exceptions |
| "Tests can come in a follow-up" | P4 | Same PR, no exceptions |
| Two unrelated features bundled "since I'm here" | P1, P3 | Split now |

### 5.3 Runnable preflight commands

Run before opening any source file. Each command surfaces a
specific gate failure mechanically.

```bash
# 1) Mini-design note exists and has the mandatory headings (§7.1)
NOTE=docs/_drafts/$(git branch --show-current).md
test -s "$NOTE" \
  && grep -q '^- Title'         "$NOTE" \
  && grep -q '^- In-scope'      "$NOTE" \
  && grep -q '^- Out-of-scope'  "$NOTE" \
  && grep -q '^- Layers'        "$NOTE" \
  && grep -q '^- Rollback'      "$NOTE" \
  && echo "design note PASSES ✓" \
  || { echo "design note INCOMPLETE ✗"; exit 1; }

# 2) Branch is feature-scoped (no main / master / detached HEAD)
B=$(git branch --show-current)
[[ -n "$B" && "$B" != "main" && "$B" != "master" ]] \
  && echo "branch $B ✓" \
  || { echo "branch is unsafe ✗"; exit 1; }

# 3) Working tree is clean except for the design note draft
git diff --quiet -- . ':!docs/_drafts' \
  && echo "no unrelated WIP ✓" \
  || { echo "uncommitted unrelated changes — commit/stash first ✗"; exit 1; }

# 4) Existing test suite is green from a clean main before you start
git stash -u >/dev/null 2>&1 || true
git checkout main -- . 2>/dev/null
pytest -q -m "not integration" >/tmp/baseline.log 2>&1 \
  && echo "baseline tests green ✓" \
  || { echo "baseline RED — fix main first ✗"; cat /tmp/baseline.log | tail -20; exit 1; }
git stash pop >/dev/null 2>&1 || true
```

If any line of the chain prints `✗`, **stop and fix that gate
first**. You cannot safely add a feature on top of a red baseline
or with an unwritten design note (P1, P2, P4 violations
guaranteed downstream).

### 5.4 Decide the feature class — literal grep recipe

If you are unsure which class your feature belongs to, run the
matrix of "what would I touch?" and let the answer pick itself:

```bash
# What files do you anticipate editing? List them, one per line, in $TOUCH.
TOUCH=/tmp/feature-touch.txt
cat > "$TOUCH" <<'PATHS'
app/bot/handlers/about.py
app/bot/keyboards/about.py
app/tests/test_bot_about.py
docs/06-bot-flow.md
PATHS

# Class hint by directory pattern (matches §4 of this doc)
grep -qE '^app/bot/'                "$TOUCH" && echo "→ involves bot layer (F-Cmd / F-Flow likely)"
grep -qE '^app/application/'        "$TOUCH" && echo "→ involves application (use case work)"
grep -qE '^app/domain/'             "$TOUCH" && echo "→ involves domain (rare; expect ADR)"
grep -qE '^app/infrastructure/providers/' "$TOUCH" && echo "→ F-Prov / F-NewProv"
grep -qE '^app/workers/'            "$TOUCH" && echo "→ F-Queue"
grep -qE '^app/application/services/delivery' "$TOUCH" && echo "→ F-Deliv"
grep -qE '^migrations/versions/'    "$TOUCH" && echo "→ F-DB (migration required)"
grep -qE '^app/config\.py|\.env'    "$TOUCH" && echo "→ F-Cfg (apply 5-place rule)"
grep -qE '^deploy/'                 "$TOUCH" && echo "→ F-Deploy (idempotency reasoning required)"
grep -qE '^docs/'                   "$TOUCH" || echo "WARN: no docs touched — P4 risk"
grep -qE '^app/tests/'              "$TOUCH" || echo "WARN: no tests touched — P4 risk"
```

If multiple class hints fire, your feature is **multi-class** —
split into separate PRs (see [§9 of `28-`](28-implementation-playbook.md#9-playbook-changing-provider-logic)
and example §24.3 of this doc).

---

## 6. How to design a feature in this project

### 6.1 Design decision tree

```mermaid
flowchart TD
    A[Feature: identified user need]
    A --> B[Define user story:<br/>'As a USER, I want WHAT, so that WHY']
    B --> C{Is there a use case for this?}
    C -- "exists already" --> D[Reuse use case;<br/>only handler/UI changes]
    C -- "new use case" --> E[Define use case interface:<br/>request DTO, response DTO, errors]
    E --> F{Needs new repository protocol?}
    F -- yes --> G[Add protocol in app/application/services/<br/>or app/domain/repositories/]
    F -- no --> H{Needs new external integration?}
    G --> H
    H -- yes --> I[Design adapter contract<br/>in app/application/services/]
    H -- no --> J{Needs background processing?}
    I --> J
    J -- yes --> K[Design arq task<br/>+ idempotency + retry policy]
    J -- no --> L{Needs state across messages?}
    K --> L
    L -- "lifetime > 1h" --> M[Postgres-backed state]
    L -- "lifetime <= 1h" --> N[Redis-backed state with TTL]
    L -- no --> O[Stateless]
    M --> P[Implementation design done]
    N --> P
    O --> P
```

### 6.2 Per-question design guidance

#### Do I need a use case?

- **Yes** if the feature involves a decision, computation,
  validation, or any orchestration of multiple sources.
- **No** if the feature is a static reply or a 1:1 thin formatter
  over an existing use case.
- **Always yes** if the feature is invoked from more than one
  surface (bot + API).

#### Do I need a worker?

- **Yes** if the work takes >2 seconds, makes external calls,
  involves yt-dlp/ffmpeg, or must survive crashes.
- **No** if the work is sub-second pure computation or a single fast
  DB read/write.

#### Do I need a queue change?

- **Only** if you're adding a new task type, changing `_job_id`
  derivation, changing retry policy, or adding a status transition.
- **Never** "re-shape the queue while you're touching the worker
  for an unrelated reason" (P7).

#### Do I need a DB schema change?

- **Yes** if the feature persists state, adds a status field, adds
  an audit row, or extends an existing entity.
- **Design** the migration shape **at design time** (additive vs
  two-step rename vs ENUM addition). See [`28-` §14](28-implementation-playbook.md#14-playbook-database-schema-change).

#### Do I need a provider change?

- **Yes** if the feature is platform-specific (a new format, a new
  source, a new metadata field).
- **Stay inside `<Platform>Provider`**. If application code must
  touch platform specifics, the design is wrong (P8).

#### Do I need a delivery change?

- **Yes** if the feature changes TTL, max-downloads, token format,
  Nginx route, or who can receive a file.
- **Highly sensitive (P11)**: review against [`17-security.md`](17-security.md)
  and `28-` §12.

#### Do I need a config / env change?

- **Yes** if the feature has any operator-tunable knob, secret, or
  feature flag.
- Apply the **five-place rule** (`28-` §15.2).

---

## 7. Feature design template

This is the **mini-design note** that every non-trivial feature
emits **before** any file edit. For trivial features (typo, label
change), skip the note but still emit the verbatim plan from
[`25-` §6.A](25-agent-guide.md#6a-plan-template-verbatim--emit-before-any-file-edit) /
[`28-` §5.1](28-implementation-playbook.md#51-planning-note-template-verbatim--emit-before-any-file-edit).

### 7.1 Mini-design note template (verbatim)

```markdown
### Feature mini-design note

- **Title:** <short verb-noun phrase>
- **Goal:** <one sentence — what user-visible thing changes>
- **User story:** As a <user>, I want <what>, so that <why>.
- **Class (§4):** <F-Cmd | F-Flow | F-Prov | F-NewProv | F-Queue | F-Deliv | F-Admin | F-Obs | F-Sec | F-Cfg>
- **Principles in play (§2):** <P1, P2, P4, …>

- **In-scope:**
  - <bullet>
  - <bullet>
- **Out-of-scope (explicit):**
  - <bullet — names a thing the user might assume is included>
  - <bullet>

- **Impacted layers:** <bot | application | domain | infrastructure | worker | api | deploy | docs>
- **Layers explicitly NOT touched:** (P1, P3) <list>

- **Data model impact:** (P10)
  - New table / column / index? <No | Yes — describe; migration shape: additive | two-step | ENUM>
  - Backwards compatibility plan? <…>

- **Queue / worker impact:** (P7)
  - New task? Retry policy? Status transitions? `_job_id` formula?
  - Idempotency story?

- **Provider impact:** (P8)
  - `BaseProvider` surface unchanged? <yes | new method — justify>
  - Platform-specific logic stays inside `<Platform>Provider`?

- **Delivery impact:** (P11)
  - TTL / max-downloads / token format / Nginx route changes?
  - Security review needed?

- **Config / env impact:** (P4)
  - New `Settings` field? Default? Secret?
  - Five-place propagation plan?

- **Deploy impact:** (P9)
  - Compose / Nginx / install script changes?
  - Migration deploy order?
  - Flag-gated rollout?

- **Logs / observability:** (P4)
  - New event names: <`feature_started`, `feature_done`, …>
  - Correlation fields: <request_id, user_id, chat_id, job_id>
  - Healthcheck affected?

- **Tests:** (P4)
  - Unit: <files / cases>
  - Integration (if any): <files / cases>
  - Negative paths: <list>

- **Docs to update in this PR:** (P4)
  - <docs/06-…> — <reason>
  - <docs/14-…> — <reason>
  - …

- **Risks / blast radius:**
  - <risk> — <mitigation>

- **Rollback story:**
  - Code revert: <safe | requires data migration?>
  - DB rollback: <downgrade defined | empty + justified>
  - Flag toggle: <yes — name | no>

- **Verification plan:**
  - Local: <commands>
  - Staging / dev: <smoke steps>
  - Production: <log queries, manual checks>
```

### 7.2 Hard rules about the design note

- **No `TBD`. No "etc."** Every bullet has a concrete value or
  `none`.
- **`Out-of-scope` and `Layers explicitly NOT touched` are mandatory
  and non-empty** — they prove the author thought about scope.
- **`Principles in play` is mandatory** — empty means the feature
  hasn't been classified.
- **If the note touches >5 layers, the feature is too big** —
  decompose into staged PRs (§23).
- See two filled-in examples in [Appendix B](#appendix-b--mini-design-note-examples).

---

## 8. Where to put new code

This section is the feature-shaped sister of [`28-` §6](28-implementation-playbook.md#6-layer-selection-guide).
It distills **what belongs in each layer for feature work**, with
DO / DON'T pairs and concrete signs of mis-placement.

### 8.1 The placement decision tree

```mermaid
flowchart TD
    A[New code for feature]
    A --> B{Pure business rule, no IO?}
    B -- yes --> DOM[app/domain/]
    B -- no --> C{Orchestrating one user intent across protocols?}
    C -- yes --> APP[app/application/use_cases/<br/>or app/application/services/]
    C -- no --> D{Talks to Postgres / Redis / Telegram /<br/>yt-dlp / ffmpeg / FS / HTTP?}
    D -- yes --> INF[app/infrastructure/]
    D -- no --> E{Parses Telegram Update or<br/>renders bot UI?}
    E -- yes --> BOT[app/bot/]
    E -- no --> F{FastAPI route?}
    F -- yes --> API[app/api/]
    F -- no --> G{arq task or schedule?}
    G -- yes --> WRK[app/workers/]
    G -- no --> H{bash / compose / nginx / certbot?}
    H -- yes --> DEP[deploy/]
    H -- no --> STOP[STOP — re-read 02-architecture.md]
```

### 8.2 What belongs where (feature view)

| Layer | What feature work goes here | What does NOT |
|---|---|---|
| **`app/domain/`** | New entity field with invariant; new enum value (paired with migration); new repo protocol; new domain exception | IO; framework imports; computing time; reading config |
| **`app/application/use_cases/`** | New user intent; orchestration; validation; new protocol declaration | ORM models; SQL; Telegram calls; reading env; redis driver imports |
| **`app/application/services/`** | New protocol an adapter must satisfy; service-level orchestrators | Concrete adapter implementations |
| **`app/infrastructure/`** | New repository impl; new adapter; new HTTP client; provider tweak; storage tweak | Business rules; UX text; routing logic |
| **`app/bot/handlers/`** | New `/command`, new callback parser; binding correlation context | DB query; yt-dlp call; reading env; multi-step orchestration |
| **`app/bot/keyboards/`** | New inline keyboard | Encoding payload (encode an id only) |
| **`app/api/`** | New `/healthz`, `/readyz`; rare new public route (use `X-Accel-Redirect`) | Business logic; `FileResponse` for public files |
| **`app/workers/tasks/`** | New arq task; new schedule; tweaked retry/backoff | Business rules (call a use case); state on `self` |
| **`deploy/`** | New service; new Nginx route; new install step; new env passthrough | Python; secrets; non-idempotent steps |
| **`docs/`** | The behaviour you just changed; the new event/exception/var | Content that already lives canonically elsewhere |
| **`app/tests/`** | A test for every new public function and every new behaviour | Real network/DB/Redis without `@pytest.mark.integration` |

### 8.3 Common placement mistakes

| Mistake | Symptom | Cure | Principle |
|---|---|---|---|
| Putting a DB query in a Telegram handler | `await session.execute(...)` in `app/bot/...` | Move to a use case, talk to a repository protocol | P5, P6 |
| Returning ORM models from a use case | Use case signature returns `<X>Model` | Map to a domain entity in the repo impl | P5 |
| Adding a top-level package (`app/services/`, `app/helpers/`) | New directory under `app/` not in the layer list | Use existing layers; if no fit → ADR | P2 |
| Hardcoding Telegram message text inside a use case | Russian/English string literals in `app/application/...` | Move text to handler/keyboard layer | P5, P6 |
| Reading env in handlers/use cases | `os.environ[...]` outside `app/config.py` | Inject via `Settings` field through the container | P5, P11 |

---

## 9. Developing a new bot command

**Class F-Cmd.** Cross-ref: [`28-` §7](28-implementation-playbook.md#7-playbook-adding-a-new-bot-command).

### 9.1 When this applies

User invokes a new `/command` (typically a static or thin-render
response).

### 9.2 Pre-flight (5 minutes)

- Confirm the command name doesn't collide with PTB built-ins or an
  existing command.
- Decide: needs a use case? (Yes if it computes / queries; no if it
  is a static message.)
- Decide: needs a keyboard? (Yes for follow-up actions.)
- Sketch the user-visible reply.

### 9.3 Design questions

- Will the command do anything if the user is rate-limited?
- What happens if the dependency (DB / Redis) is down?
- Is the command admin-only? If yes, where does authz live?

### 9.4 Files typically touched

| File | Change |
|---|---|
| `app/bot/handlers/<feature>.py` | New `async def handle_<command>(update, context)` |
| `app/bot/application_factory.py` | Register handler with PTB |
| `app/application/use_cases/<feature>.py` (if needed) | New use case |
| `app/exceptions.py` (if needed) | New `AppError` subclass |
| `docs/06-bot-flow.md` | Handler catalogue row |
| `docs/16-error-handling.md` (if new exception) | Catalogue row |
| `app/tests/test_bot_<feature>.py` | Handler test with fakes |

### 9.5 Step-by-step

1. Emit the §7 mini-design note.
2. Implement the use case (if needed) inside-out.
3. Implement the handler — bind context, call the use case, render.
4. Register handler in `application_factory.py`.
5. Add handler test with a fake `Update` and a fake use case.
6. Update `06-` and `16-` (if needed).
7. Run §22 completion checklist.

### 9.6 DO / DON'T quick reference

| Do | Don't |
|---|---|
| ✅ Bind correlation context (`request_id`, `user_id`, `chat_id`) | ❌ Read DB / Redis from inside the handler (P5, P6) |
| ✅ Delegate to one use case | ❌ Inline 3+ business steps in the handler |
| ✅ Render `error.user_message` for `AppError` | ❌ Reply with `str(exc)` or a traceback (P11) |
| ✅ Update `06-` handler catalogue same PR | ❌ Defer doc update (P4) |

### 9.7 Common mistakes

- Forgot to register the handler — silent no-op.
- Copy-pasted from another handler and inherited an unused dependency.
- Used a string callback prefix that collides with another flow.

---

## 10. Developing a new callback-driven flow

**Class F-Flow.** Cross-ref: [`28-` §8](28-implementation-playbook.md#8-playbook-adding-a-new-user-flow).

### 10.1 When this applies

A multi-step interaction (e.g., "paste URL → choose quality →
confirm → receive file") that needs state across messages.

### 10.2 Pre-flight (15 minutes)

- Sketch the **state diagram** explicitly. If you can't, the flow
  is under-designed.
- Choose the state store: PTB `chat_data` is ephemeral and lost on
  restart — prefer Redis with TTL, or Postgres if state must
  outlive the conversation.
- Plan for **stale callback data** (the user clicked a button from
  a 3-day-old message — handle it gracefully).

### 10.3 Design questions

- What is the TTL of each state? Choose explicitly (Redis: minutes
  to hours; Postgres: hours to forever).
- What happens if the user clicks a stale button?
- What happens if the user starts a new flow mid-flow?
- How is state cleaned up on success / failure / abandonment?

### 10.4 Files typically touched

| File | Change |
|---|---|
| `app/bot/handlers/<flow>.py` | One handler per state transition |
| `app/bot/keyboards/<flow>.py` | Inline keyboards for each step |
| `app/bot/callbacks/<flow>.py` | Callback codec |
| `app/application/services/<flow>.py` | Use case per transition |
| `app/application/dto/<flow>.py` | DTOs |
| `app/infrastructure/state_store.py` | If short-lived state |
| `app/infrastructure/db/...` | If persistent state |
| `app/exceptions.py` | New errors |
| `docs/06-bot-flow.md` | Flow diagram |
| `docs/14-logging-observability.md` | New events (`<flow>_step_started`, …) |
| `app/tests/test_<flow>.py` | One test per transition |

### 10.5 DO / DON'T quick reference

| Do | Don't |
|---|---|
| ✅ Sketch the state diagram before code | ❌ Discover state shape mid-implementation (P2) |
| ✅ Encode only an **id** in `callback_data` | ❌ Stuff full state into 64 bytes (P6) |
| ✅ Choose state TTL explicitly | ❌ Rely on PTB `chat_data` alone (P7) |
| ✅ Namespace state by `user_id`/`chat_id` | ❌ Share state across users (P11) |
| ✅ Cleanup on every error path | ❌ Leak state with no TTL fallback (P9) |
| ✅ Document the flow with a mermaid state diagram | ❌ Leave the new flow undocumented (P4) |

### 10.6 Common mistakes

- Encoded the full payload in `callback_data` — works in dev (small
  payloads), explodes in prod (>64 bytes).
- Forgot to clear state on error — state leaks until TTL expiry,
  user gets confusing UX.
- State namespaced by `chat_id` only in a group chat — leaks across
  users.

---

## 11. Developing a provider-related feature

**Class F-Prov or F-NewProv.** Cross-ref: [`28-` §9](28-implementation-playbook.md#9-playbook-changing-provider-logic) /
[`28-` §10](28-implementation-playbook.md#10-playbook-adding-a-new-provider) /
[`30-add-new-provider-guide.md`](30-add-new-provider-guide.md).

### 11.1 When this applies

- A new download option for an existing platform.
- Better metadata extraction for an existing provider.
- A new platform behaviour (Instagram gallery, YouTube live, …).
- Adding a new platform entirely.

### 11.2 Pre-flight (20 minutes)

- Run `yt-dlp -j <sample-url>` and inspect the **actual** JSON. Do
  not guess.
- Identify ≥3 URL shapes (canonical, share, mobile).
- Confirm the platform is supported by yt-dlp at all.
- Decide whether `BaseProvider` surface stays unchanged (it should).

### 11.3 Design questions

- Will this change break in-flight `DownloadOption.key` callbacks?
- Is the new behaviour platform-specific (stays in the provider)
  or cross-platform (lives in a use case)?
- Are there auth / cookie / rate-limit concerns?

### 11.4 Files typically touched

See [`28-` §9.2 / §10.2](28-implementation-playbook.md#92-files-typically-touched).

### 11.5 DO / DON'T quick reference

| Do | Don't |
|---|---|
| ✅ Inspect real `yt-dlp -j` output before coding | ❌ Guess at field names (P8) |
| ✅ Add / refresh recorded JSON fixture | ❌ Test against live yt-dlp (P4) |
| ✅ `info.get(key, default)` for every yt-dlp field | ❌ `info["format"]` direct access (P8) |
| ✅ Keep `BaseProvider` surface unchanged | ❌ Add a public method only this provider needs (P8) |
| ✅ Preserve existing `DownloadOption.key` strings | ❌ Rename keys silently (in-flight callbacks break) (P8) |
| ✅ Cover failure modes (geo, age, empty `formats`) | ❌ Test only the happy path (P4) |

### 11.6 Common mistakes

- Trusted yt-dlp to expose a key that doesn't always exist.
- Renamed a `DownloadOption.key` and broke pending callbacks.
- Added platform-specific branches in `app/application/...` instead
  of inside the provider.

---

## 12. Developing a queue/worker feature

**Class F-Queue.** Cross-ref: [`28-` §13](28-implementation-playbook.md#13-playbook-changing-queue--worker-behaviour).

### 12.1 When this applies

- A new background task type.
- A new status field or transition in `download_jobs`.
- A change to retry policy or backoff strategy.
- A new scheduled job.

### 12.2 Pre-flight (15 minutes)

- Check whether the work truly needs to be async (>2s, external
  call, can crash).
- Decide `_job_id` formula — must be deterministic for idempotency.
- Decide `max_tries` — must be finite.
- List the failure modes: transient / rate-limit / permanent
  user-error / programmer error.

### 12.3 Design questions

- Is the task safe to retry **at any moment**, on any try?
- Does the task have side effects that must be idempotent (e.g.,
  sending a Telegram message)?
- What's the user-visible status reporting story?

### 12.4 Files typically touched

See [`28-` §13.2](28-implementation-playbook.md#132-files-typically-touched).

### 12.5 DO / DON'T quick reference

| Do | Don't |
|---|---|
| ✅ Deterministic `_job_id = f"job:{job.id}"` | ❌ `_job_id` with randomness or timestamps (P7) |
| ✅ Bounded `max_tries` (3–5) | ❌ `max_tries=None` (P7) |
| ✅ Distinguish transient / rate-limit / permanent / programmer | ❌ Treat every exception as retryable (P7) |
| ✅ Stateless workers — no module-level state | ❌ Module-level `_cache: dict = {}` (P7) |
| ✅ Write idempotency marker before exit | ❌ Make retries double-side-effect (P7) |
| ✅ Log each status transition with structured fields | ❌ Update DB silently (P4) |

### 12.6 Common mistakes

- Caught `Exception` "to keep workers alive" — kills retry signal,
  silently loses jobs.
- Stored `MediaInfo` in a process-local dict — works on one worker,
  inconsistent with two.
- `_job_id` derived from current time — every retry creates a new
  job.

---

## 13. Developing a delivery / temp-link feature

**Class F-Deliv.** Cross-ref: [`28-` §12](28-implementation-playbook.md#12-playbook-changing-delivery-logic).

### 13.1 When this applies

- TTL / max-downloads change.
- New header on the public download.
- Token format change.
- New rate limit on `/d/{token}`.
- Expiry warning to user.

### 13.2 Pre-flight (15 minutes)

- Re-read [`17-security.md`](17-security.md) §5.
- Confirm `internal;` stays on the storage `location`.
- Confirm the change does not loosen any security default.

### 13.3 Design questions

- Does this widen the public attack surface?
- Does this affect in-flight tokens? If yes, version the token.
- Does this change the rate-limit zone?

### 13.4 Files typically touched

See [`28-` §12.2](28-implementation-playbook.md#122-files-typically-touched).

### 13.5 DO / DON'T quick reference

| Do | Don't |
|---|---|
| ✅ `X-Accel-Redirect` against `internal` Nginx location | ❌ `FileResponse(path)` (P11) |
| ✅ Validate token + TTL + counter on every request | ❌ Trust URL shape alone (P11) |
| ✅ Log only last-4 chars of token | ❌ Log full token (P11) |
| ✅ Decrement / mark-used in one transaction | ❌ Two-step decrement (race) (P7, P11) |
| ✅ Justify any TTL or counter change in PR | ❌ Loosen security defaults silently (P11) |
| ✅ Preserve security headers + rate limits in Nginx | ❌ Drop a header to "simplify" (P11) |

### 13.6 Common mistakes

- Increased TTL "since users complain" without any threat-model
  review.
- Added a query-parameter alternative for the token (broadens
  surface).
- Cached token-lookup in a process-local dict.

---

## 14. Developing a DB-backed feature

**Class F-DB (overlap with most other classes).** Cross-ref:
[`28-` §14](28-implementation-playbook.md#14-playbook-database-schema-change).

### 14.1 When this applies

The feature persists state, adds an audit row, extends an entity,
introduces a status, or stores user-specific data.

### 14.2 Pre-flight (20 minutes)

- Decide migration **shape** at design time:
  - **Additive** (new nullable column, new table, new index): safe,
    one-step.
  - **ENUM addition**: needs explicit `ALTER TYPE … ADD VALUE`
    (autogen misses this).
  - **Two-step destructive** (rename, drop): never one PR.
- Plan the deploy order: migration first, code second; or new code
  reads both shapes.

### 14.3 Design questions

- Is the schema change **compatible with both old and new code**?
- Do we need backfill? (If yes, do it in SQL inside `upgrade()`.)
- What's the rollback story? (Define `downgrade()` or justify
  empty.)
- Does any read path need an index for the new query?

### 14.4 Files typically touched

See [`28-` §14.3](28-implementation-playbook.md#143-files-typically-touched).

### 14.5 DO / DON'T quick reference

| Do | Don't |
|---|---|
| ✅ Always write a NEW migration | ❌ Edit a previously-shipped migration (P10) |
| ✅ Hand-review autogen diff | ❌ Trust autogen for ENUMs/renames (P10) |
| ✅ Provide `downgrade()` body or comment-justified empty | ❌ Bare `pass` with no comment (P10) |
| ✅ Two-step destructive (add → dual-write → backfill → drop) | ❌ One-step drop (P10) |
| ✅ Migration compatible with both old and new code | ❌ Migration that breaks live old code (P9, P10) |
| ✅ Test apply on fresh DB AND on top of current head | ❌ "It worked once locally" (P10) |

### 14.6 Common mistakes

- Added a `NOT NULL` column without `DEFAULT` to a populated table.
- Renamed a column directly — must use four-step pattern.
- Coupled schema migration with business behaviour in the same PR
  — split (see §24.5 worked example).

---

## 15. Developing a config-driven feature

**Class F-Cfg (often overlay with another class).** Cross-ref:
[`28-` §15](28-implementation-playbook.md#15-playbook-configuration--environment-change).

### 15.1 When this applies

The feature has a knob — a flag, a threshold, a secret, an
external URL.

### 15.2 Pre-flight (5 minutes)

- Pick a name: `<SCREAMING_SNAKE>`, prefixed by subsystem if
  possible (`PROVIDER_TIKTOK_COOKIES`, not `COOKIES`).
- Pick a default that is **safe** (off / minimal). Production
  overrides via `.env`.
- If secret: install script must generate / prompt for it (P11).

### 15.3 Design questions

- Is the default safe to ship to a fresh installation? (The feature
  starts off if unsure.)
- Is the var consumed in 3+ unrelated subsystems? (If yes, it's
  probably 3 vars, not 1.)
- Is there validation? (Use `pydantic.Field(..., ge=, le=, pattern=...)`.)

### 15.4 The five-place rule (mandatory)

Every new env var lives in **all** of:

1. `app/config.py` — typed `Settings` field.
2. `.env.example` — repo-root template.
3. `deploy/single/.env.example` и `deploy/nl1/.env.example` и/или `deploy/nl2/.env.example`.
4. `environment:` block в соответствующем фрагменте `deploy/compose/{control,media}.yml` (стеки `deploy/{single,nl1,nl2}` только `include` их).
5. `docs/13-config-and-env.md`.

If secret: also `deploy/scripts/install.sh`.

### 15.5 DO / DON'T quick reference

| Do | Don't |
|---|---|
| ✅ Add the var in all FIVE places | ❌ Add only to `.env.example` (P4) |
| ✅ Strong typed default + validation in `Settings` | ❌ `os.environ["NAME"]` outside `app/config.py` (P5, P11) |
| ✅ Generate / prompt for secrets in `install.sh` | ❌ Hard-code secret defaults (P11) |
| ✅ Document purpose, default, consumer in `13-` | ❌ Skip docs (P4) |
| ✅ Override `Settings` field in tests via fixture | ❌ `monkeypatch.setenv` per test (P4) |

### 15.6 Common mistakes

- Var added everywhere except compose `environment:` — works in
  dev, missing in prod.
- Default chosen as the "production value" — fresh install gets
  unexpected behaviour.
- No validation — bad value crashes the app at runtime instead of
  startup.

---

## 16. Logging requirements for new features

### 16.1 What to log

For every new feature:

| Event class | When to emit | Level | Required fields |
|---|---|---|---|
| **Entry** (`<feature>_started`) | At the start of a unit of work | INFO | `request_id` / `job_id`, `user_id`, `chat_id` |
| **Decision** (`<feature>_chose_<option>`) | At a branch point worth searching for | INFO | context + the decision input |
| **External call** (`yt_dlp_started`, `ffmpeg_done`) | Around external subprocess / HTTP | INFO | identifiers + duration on completion |
| **Handled error** (`<feature>_failed_user_error`) | A user-meaningful failure caught and rendered | WARNING | error code, sanitized message |
| **Unhandled error** | An unexpected exception | ERROR / `exception` | full traceback (no secrets) |
| **Completion** (`<feature>_done`) | At successful exit of a unit of work | INFO | identifiers + outcome |

### 16.2 What NOT to log

- The `BOT_TOKEN`, DB password, Redis password, any secret.
- Full token-bearing URLs (log host + last-4 of token).
- Raw cookies, `Authorization` headers.
- Full Telegram `Update` objects (PII).
- Loop iterations (log explosion).

### 16.3 Identifiers to include (minimum)

- `request_id` for any user-initiated work (bind at handler).
- `job_id` for any worker-initiated work (bind in task body).
- `user_id`, `chat_id` for bot-side work.
- `provider`, `platform` for download-related work.
- `token_tail` (last 4 chars) for delivery-related work.

### 16.4 DO / DON'T

| Do | Don't |
|---|---|
| ✅ Constant `snake_case` event name | ❌ F-string event name (`f"job_{job_id}_done"`) (P4) |
| ✅ Bind correlation context once at the top | ❌ Pass `job_id` through every call (P4) |
| ✅ Structured fields | ❌ Concatenated message strings (P4) |
| ✅ INFO for transitions, WARNING for handled errors, ERROR for programmer errors | ❌ ERROR for expected user errors (alert noise) (P4) |
| ✅ Strip secrets before logging | ❌ Log full URLs / cookies / payloads (P11) |
| ✅ Add new event names to `14-` catalogue same PR | ❌ Land code that emits unfindable events (P4) |

---

## 17. Testing requirements for new features

The unit-test bar is non-negotiable: every new public function and
every new behaviour has at least one test. Per feature class,
additional obligations apply.

### 17.1 Per-class test obligations

| Class | Mandatory tests |
|---|---|
| **F-Cmd** | Handler test with fake `Update` + fake use case |
| **F-Flow** | One test per state transition; codec round-trip |
| **F-Prov** | Provider unit test against recorded fixture; URL detection (≥3 shapes); failure modes (geo, age, empty `formats`) |
| **F-NewProv** | All of F-Prov + ENUM presence test + composition wiring test |
| **F-Queue** | Inner-function test with fake `ctx`; idempotent retry test (a 2nd run does not double-side-effect) |
| **F-Deliv** | Service unit test; public-route test with fake storage; `nginx -t` |
| **F-Admin** | Authz test (non-admin refused); destructive-op confirm test |
| **F-Obs** | Structured-field round-trip test; level + name in catalogue |
| **F-Sec** | Refused-input test (proves the tightening); UX-still-works test (proves no regression) |
| **F-Cfg** | `Settings` validation test; default-applied test |

### 17.2 Hermetic-test requirement

- No real network, no real DB, no real Redis without
  `@pytest.mark.integration`.
- Use the protocol-based fakes in `app/tests/conftest.py`.
- Async tests must not `time.sleep` — use `asyncio.sleep` or a fake
  clock.

### 17.3 Edge-case tests to include

- Empty input.
- Maximum-size input.
- User clicks a stale callback button (state is gone).
- External call times out.
- External call returns malformed payload.
- Two concurrent invocations from the same user.
- The "happy path with everything turned off" (feature flag off,
  ensures nothing leaks).

### 17.4 Negative-path tests

For every error class:

- Test that the right `AppError` subclass is raised.
- Test that the user-facing message is the expected one.
- Test that the log event is emitted with the expected level and
  fields.
- Test that no side effect leaked (e.g., no row in DB, no file on
  disk).

### 17.5 DO / DON'T

| Do | Don't |
|---|---|
| ✅ Test against protocol fakes | ❌ Test against the real driver (P4) |
| ✅ Mark integration tests | ❌ Mix integration and unit in CI (P4) |
| ✅ Test error paths as carefully as happy paths | ❌ Ship "happy path coverage only" (P4) |
| ✅ Reset state between tests | ❌ Rely on test-order side effects (P4) |
| ✅ Keep tests fast (<1s per unit test) | ❌ Add `time.sleep(5)` "to be safe" (P4) |

### 17.6 Runnable test commands

Concrete invocations per feature class. Copy-paste, do not
paraphrase.

**Run only your feature's tests by keyword (fastest dev loop):**

```bash
KW=about                  # short keyword tied to your feature
pytest -x -q -k "$KW"
```

**Per-class default invocations:**

| Class | Command |
|---|---|
| F-Cmd / F-Flow | `pytest -x -q app/tests/test_bot_*.py` |
| F-Prov / F-NewProv | `pytest -x -q app/tests/test_providers_*.py app/tests/test_url_detection.py` |
| F-Queue | `pytest -x -q app/tests/test_workers_*.py app/tests/test_use_cases_*.py` |
| F-Deliv | `pytest -x -q app/tests/test_delivery_*.py app/tests/test_temp_links_*.py` |
| F-DB | `pytest -x -q app/tests/test_repositories_*.py app/tests/test_use_cases_*.py` |
| F-Cfg | `pytest -x -q app/tests/test_config.py` |

**Coverage gate (≥ 90% on the changed files only):**

```bash
CHANGED=$(git diff --name-only origin/main...HEAD -- 'app/**/*.py' \
          | grep -v '/tests/' | tr '\n' ',' | sed 's/,$//')
test -n "$CHANGED" \
  && pytest -q --cov=app --cov-report=term-missing app/tests/ \
       | grep -E "$(echo "$CHANGED" | tr ',' '|')|TOTAL"
# Inspect every changed file's coverage line — target ≥ 90%.
```

**Migration tests (F-DB) — apply against a throwaway DB:**

```bash
docker run -d --rm --name pgtest -e POSTGRES_PASSWORD=test \
  -p 55432:5432 postgres:16 >/dev/null
sleep 3
DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres \
  alembic upgrade head \
  && DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres \
       alembic downgrade -1 \
  && DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres \
       alembic upgrade head \
  && echo "migration is reversible (or downgrade is intentionally no-op) ✓"
docker stop pgtest >/dev/null
```

**Confirm tests do not hit the network (P4):**

```bash
grep -nE '^(import|from) (requests|aiohttp|httpx|yt_dlp)( |$)' \
  app/tests/ | grep -v 'yt_dlp.utils' | grep -v 'fakes/'
# expected: empty
```

**Pre-merge "all greens" one-liner:**

```bash
ruff format --check . \
  && ruff check . \
  && mypy app \
  && pytest -q -m "not integration" \
  && echo "PRE-MERGE OK ✓"
```

If any line prints anything other than `OK` or green dots, **do
not open the PR**.

---

## 18. Documentation requirements for new features

A feature is **not done** until its documentation is updated in
the same PR. This is the most-skipped step and the most expensive
one to skip (future agents redo your work).

### 18.1 Per-feature-type doc map

| Feature class | Docs to update |
|---|---|
| **F-Cmd** | `06-bot-flow.md` (handler catalogue), `16-error-handling.md` if new exception |
| **F-Flow** | `06-bot-flow.md` (flow diagram), `14-logging-observability.md` (events) |
| **F-Prov** | `07-provider-architecture.md` (provider table) |
| **F-NewProv** | `07-`, `30-add-new-provider-guide.md`, `12-` (ENUM), `13-` if auth, `14-` events |
| **F-Queue** | `09-queue-and-workers.md`, `14-` events, `24-runbooks.md` if new failure mode |
| **F-Deliv** | `10-temp-links-and-delivery.md`, `17-security.md` |
| **F-Admin** | `06-`, `17-`, `24-` |
| **F-Obs** | `14-`, `15-` if new probe |
| **F-Sec** | `17-`; ADR if surface changes |
| **F-Cfg** | `13-config-and-env.md` |

### 18.2 The cross-cutting doc map (`28-` §22)

Every feature also reviews `28-` §22 ("What must be updated
together"). If something on that table moved, update it.

### 18.3 When to write an ADR

Write a new ADR if the feature:

- Touches an ADR-0005 §2 row (locked decision) — **mandatory**
  superseding ADR.
- Introduces a new public surface, a new external dependency, a
  new architectural pattern, or a new layering rule.
- Loosens a security default (P11) — **mandatory** ADR.

If unsure, draft the ADR; the cost is low and the trace value is
high.

### 18.4 DO / DON'T

| Do | Don't |
|---|---|
| ✅ Update every doc affected per §18.1 + `28-` §22 in **the same PR** | ❌ Defer docs to a follow-up (P3, P4) |
| ✅ Update `docs/README.md` index if a new doc file is created | ❌ Silent-add a doc that isn't indexed (P4) |
| ✅ Update `docs/adr/README.md` index if a new ADR is created | ❌ Silent-add an ADR (P4) |
| ✅ Use existing canonical docs; don't fork content | ❌ Create `docs/feature-x.md` if it duplicates `docs/06-…` (P3) |

---

## 19. Deployment awareness during feature development

The deploy story is part of the feature design, not an
afterthought.

### 19.1 When the feature requires compose changes

- A new service.
- A new env passthrough.
- A new healthcheck.
- A new volume mount.

→ Apply [`28-` §18](28-implementation-playbook.md#18-playbook-docker--compose-change) rules.

### 19.2 When the feature requires Nginx changes

- A new public route.
- A new security header.
- A new rate-limit zone.
- A storage `location` change.

→ Apply [`28-` §19](28-implementation-playbook.md#19-playbook-nginx--ssl--certbot-change) rules.
**Never** remove `internal;` from the storage location.

### 19.3 When the feature requires migration ordering

If the feature has a DB schema change:

1. **PR1** ships the migration + write path.
2. Migration is deployed first.
3. **PR2** ships the read path / UI.
4. Document the dependency in PR1's description ("requires deploy
   on production before PR #N").

### 19.4 When the feature requires secrets / env updates

- Add to **all five places** (§15.4).
- If new secret: update `deploy/scripts/install.sh`.
- Document rotation procedure in `17-security.md`.

### 19.5 When the feature requires rollout caution

Flag-gate the feature when:

- It touches a hot path (download, delivery).
- It changes user-visible behaviour for existing flows.
- It introduces a new external call.
- It is observably risky and benefits from a kill switch.

Flag pattern:

```python
# app/config.py
feature_x_enabled: bool = Field(default=False)

# app/application/use_cases/x.py
if not settings.feature_x_enabled:
    return _legacy_path(...)
return _new_path(...)
```

### 19.6 DO / DON'T

| Do | Don't |
|---|---|
| ✅ Migrations deployed first; code that depends on them in a follow-up PR | ❌ One PR with migration + dependent code (P9, P10) |
| ✅ Risky features behind a `Settings` flag, default-off | ❌ Ship a kill-switch-less hot-path change (P9) |
| ✅ Document the deploy order in PR description | ❌ Assume reviewer knows the order (P4, P9) |
| ✅ Add post-deploy verification (log query, smoke check) | ❌ Declare deploy done on green pipeline alone (P9) |

---

## 20. Common feature-development anti-patterns

### 20.1 Scope and rhythm

- ❌ **Overbuilding** — adding "while we're here" capabilities the
  user didn't ask for. **P1, P3.**
- ❌ **Hidden refactor** — bundling refactor + feature in the same
  PR. **P1, P3.**
- ❌ **Reformatting unrelated files**. **P3.**
- ❌ **Renaming opportunistically**. **P1, P3.**
- ❌ **Skipping the §7 mini-design note**. **P1, P2.**

### 20.2 Wrong-layer placement

- ❌ Business logic in a Telegram handler. **P6.**
- ❌ DB call inside a handler. **P5, P6.**
- ❌ ORM model returned from a use case. **P5.**
- ❌ Telegram message text inside a use case. **P5, P6.**
- ❌ Reading env outside `app/config.py`. **P5, P11.**

### 20.3 Provider / pipeline

- ❌ Mixing Telegram presentation with provider logic. **P6, P8.**
- ❌ Calling `yt-dlp` outside `<Platform>Provider`. **P8.**
- ❌ Trusting yt-dlp output schema. **P8.**
- ❌ Renaming a `DownloadOption.key` silently. **P8.**

### 20.4 Queue / worker

- ❌ Silent `_job_id` change. **P7.**
- ❌ `except Exception: pass` to "keep workers alive". **P7.**
- ❌ Module-level state in worker. **P7.**
- ❌ Unbounded retries. **P7.**

### 20.5 DB

- ❌ Schema change without migration. **P10.**
- ❌ Editing a shipped migration. **P10.**
- ❌ One-step destructive (drop column same time as code stops
  writing). **P10.**
- ❌ Trusting autogen for ENUMs. **P10.**

### 20.6 Deploy / config

- ❌ Non-idempotent deploy step. **P9.**
- ❌ Env var added in only one place. **P4.**
- ❌ Compose change losing healthcheck / restart / anchors. **P9.**
- ❌ Public port on NL-1 (в `single` — у любого сервиса, кроме `nginx`). **P11.**
- ❌ `internal;` removed from storage Nginx location. **P11.**

### 20.7 Subprocess / paths / IO

- ❌ `subprocess.run(..., shell=True)`. **P11.**
- ❌ `os.path.join(STORAGE_PATH, untrusted)`. **P11.**
- ❌ Skipped `sanitize_filename`. **P11.**

### 20.8 Observability

- ❌ Logging full URLs / tokens / secrets. **P11.**
- ❌ F-string event names. **P4.**
- ❌ Adding new event without catalogue update. **P4.**
- ❌ Missing correlation context. **P4.**

### 20.9 Tests / docs

- ❌ Behaviour change without a doc update in same PR. **P4.**
- ❌ Behaviour change without a test in same PR. **P4.**
- ❌ Skipping a flaky test. **P4.**
- ❌ "Happy path only" test coverage. **P4.**

### 20.10 Operational

- ❌ No rollback story documented. **P9.**
- ❌ Risky feature shipped without kill switch. **P9.**
- ❌ Migration + dependent code in same PR. **P9, P10.**
- ❌ No post-deploy verification. **P9.**

---

## 21. Common mistakes by AI agents

These are the high-frequency slips observed in past AI-authored
feature PRs. Each row carries the violated principle and the
cheapest cure.

| # | Mistake | Symptom | Cure | Principle |
|---|---|---|---|---|
| 1 | Started coding without §7 mini-design note | Files outside scope appear; reviewer asks "what is this PR doing?" | Revert; emit the design note now | P1, P2 |
| 2 | Too broad scope — bundled multiple features | Diff touches 3+ unrelated subsystems | Split into one PR per feature class | P1, P3 |
| 3 | "Drive-by" edits to unrelated files | Whitespace / import-order changes in files unrelated to the goal | `git checkout --` the unrelated edits | P3 |
| 4 | DB call inside Telegram handler | `from app.infrastructure...` in `app/bot/...` | Move logic to a use case | P5, P6 |
| 5 | Returned ORM model from a use case | Use case sees `<X>Model` | Map to domain entity in repo impl | P5 |
| 6 | Forgot to register a new arq task | Worker doesn't pick up the task | Add to `WorkerSettings.functions` | P7 |
| 7 | Silent `_job_id` format change | In-flight jobs become orphans | Revert; if needed, version it | P7 |
| 8 | `subprocess.run(..., shell=True)` | `shell=True` in diff | Switch to `create_subprocess_exec(arg, arg)` | P11 |
| 9 | Logged a full URL containing a token | Token-bearing URL in log fields | Strip; log host + last-4 only | P11 |
| 10 | Added env var only to `.env.example` | App reads `None` in prod | Add to all five places (§15.4) | P4 |
| 11 | Forgot ENUM `ALTER TYPE` in migration | Autogen migration missing it | Hand-edit migration | P10 |
| 12 | Edited a previously-shipped migration | Diff to `migrations/versions/<old>.py` | Revert; write new migration | P10 |
| 13 | Caught `Exception` to make a test pass | `except Exception: pass` in diff | Remove; fix the underlying issue or update test contract | P7, P11 |
| 14 | Wrong assumption about provider output | Crashed on missing yt-dlp key | `.get()` with defaults; assert against fixture | P8 |
| 15 | Cached `MediaInfo` in a process-local dict | Module-level `_cache: dict = {}` | Move to Redis with TTL or remove | P7 |
| 16 | Created a new top-level package (`app/services/`, `app/helpers/`, …) | New directory under `app/` not in the layer list | Use existing layers; if no fit → ADR | P2 |
| 17 | Stuffed >64 bytes in `callback_data` | Telegram refuses the keyboard | Persist in DB / Redis; encode an id | P6 |
| 18 | `pytest.mark.skip` to bypass a flaky test | Skipped test in diff | Fix the flake or delete the test with justification | P4 |
| 19 | Streamed a public file via `FileResponse` | `FileResponse(path)` in `app/api/public/...` | Use `X-Accel-Redirect` against `internal` Nginx location | P11 |
| 20 | Forgot the temp-file cleanup story | Disk fills up after partial failures | Cleanup task is the safety net; tests cover failure paths | P9 |
| 21 | Skipped the docs update | Future agents redo your work | Update docs **same PR**; review §18.1 + `28-` §22 | P4 |
| 22 | "Happy path only" tests | Edge cases break in prod | Add negative-path tests per §17.4 | P4 |
| 23 | Healthcheck talks to DB on every request | Pool exhaustion | Cache result for a short window | P9 |
| 24 | New dependency added without asking | New line in `pyproject.toml` | Revert; surface to user; await approval | P1, P2 |
| 25 | Loosened security default for "perf" | TLS / HSTS / rate-limit relaxed | Revert; require ADR if truly needed | P11 |

If the diff hits **two or more** rows, halt and re-plan — the
feature is signalling a misunderstanding, not a coding slip.

---

## 22. Feature completion checklist

A feature is **done** when **every** box below is ticked, or each
unticked box has an explicit waiver in the PR description with
reviewer sign-off.

### 22.1 Goal & scope

- [ ] Feature goal met (per the design note's "Goal").
- [ ] User flow works end-to-end (manual smoke).
- [ ] In-scope items all delivered.
- [ ] Out-of-scope items explicitly NOT delivered.
- [ ] No drive-by edits in the diff.

### 22.2 Architecture

- [ ] No layer-direction violation (P5).
- [ ] No business logic in handlers (P6).
- [ ] No new top-level package under `app/` (P2).
- [ ] `app/composition/` updated if a new dependency wired (P2).
- [ ] No locked decision violated (or: superseding ADR in this PR).

### 22.3 Tests

- [ ] At least one test per public function added.
- [ ] Per-class test obligations from §17.1 met.
- [ ] Negative paths tested (per §17.4).
- [ ] Tests are hermetic.
- [ ] `pytest -m "not integration"` green locally.

### 22.4 Docs

- [ ] Every doc per §18.1 + `28-` §22 updated in this PR.
- [ ] `docs/README.md` updated if a new doc file was created.
- [ ] `docs/adr/README.md` updated if a new ADR was created.
- [ ] No placeholder sections (`## TBD`).

### 22.5 Logging / observability

- [ ] New code paths an on-call engineer would search for have log
      events in `14-` catalogue.
- [ ] Correlation context bound at the top of every async unit.
- [ ] No PII / token / cookie / secret in logs.

### 22.6 Config

- [ ] If new env var: present in **all five places** (§15.4).
- [ ] If secret: install script generates / prompts for it.
- [ ] Default is safe (off / minimal).

### 22.7 Queue / worker

- [ ] If new task: `_job_id` deterministic; `max_tries` finite.
- [ ] Idempotency story: a retry does not double-side-effect.
- [ ] Status transitions logged.

### 22.8 DB

- [ ] If schema change: hand-reviewed migration; `downgrade()` (or
      justified empty); deploy-order note in PR.
- [ ] Migration applied on fresh DB AND on top of current head
      locally.

### 22.9 Deploy

- [ ] Compose / Nginx / install changes follow `28-` §§18–20 rules.
- [ ] No new public port on NL-1 (в `single` — только `nginx` публикует 80/443).
- [ ] No security default loosened.
- [ ] Nginx `internal;` preserved.

### 22.10 Operational safety

- [ ] Rollback story documented (code revert + DB rollback +
      flag-toggle option).
- [ ] If risky: feature is flag-gated, default off.
- [ ] PR description matches `28-` §3.3 template.

### 22.11 Principles compliance (P1–P11) gate

- [ ] **P1** — minimal safe scope respected.
- [ ] **P2** — architecture-first; layer + ADR-0005 audit done.
- [ ] **P3** — no unrelated changes.
- [ ] **P4** — docs / tests / config updated together.
- [ ] **P5** — no infrastructure leakage into domain / application.
- [ ] **P6** — no business logic in handlers.
- [ ] **P7** — queue semantics preserved.
- [ ] **P8** — provider contracts explicit.
- [ ] **P9** — deploy idempotency reasoned.
- [ ] **P10** — DB migration reasoned.
- [ ] **P11** — security defaults preserved.

A single unticked P box = "not done".

---

## 23. Feature rollout checklist

A green PR is necessary but not sufficient. Use this checklist to
**land the feature on production safely**.

### 23.1 Pre-merge

- [ ] §22 completion checklist green.
- [ ] CI green (lint + typecheck + unit tests + shellcheck + image
      build).
- [ ] PR description matches `28-` §3.3 template.
- [ ] Reviewer signoff (or self-signoff with explicit reasoning if
      solo).
- [ ] Any P-gate waiver explicitly documented.

### 23.2 Pre-deploy

- [ ] Latest `main` rebuilt; image tagged.
- [ ] Backup of Postgres taken (per `22-backup-restore.md`).
- [ ] Migration plan reviewed (if any): order + rollback path.
- [ ] Compose / Nginx config validated locally
      (`docker compose config`, `nginx -t`).
- [ ] Operator notified (if multi-operator team).

### 23.3 Migration step (if applicable)

- [ ] Migration applied first, before deploying dependent code.
- [ ] `alembic history` matches expected revision.
- [ ] Old code (currently running) still operates against the new
      schema (verify in a brief grace window).
- [ ] If migration adds a new column / index — basic query smoke
      test (`SELECT … LIMIT 5`).

### 23.4 Deploy

- [ ] Deployed to `single` или NL-1 / NL-2 (per `20-deployment.md`).
- [ ] Containers reach `healthy`.
- [ ] No restart loops in the first 5 minutes.
- [ ] `/readyz` returns 200 from each plane.

### 23.5 Post-deploy validation

- [ ] Manual smoke: small file end-to-end (Telegram).
- [ ] Manual smoke: large file end-to-end (temp link).
- [ ] Logs show new event names with the expected fields.
- [ ] No new ERROR-level events for the new feature in the first
      10 minutes.
- [ ] Healthchecks stable.
- [ ] If flag-gated: enable the flag for an internal channel only;
      observe for 30 minutes; then enable broadly.

### 23.6 Rollback triggers

If any of these is true, **roll back immediately** (do not debug
on production):

- ERROR-rate spike (>baseline) for the new feature's events.
- Healthcheck flapping.
- Worker restart loop.
- DB connection pool exhaustion.
- Public download success rate drop.
- Telegram `429 Too Many Requests` rate spike.

Rollback steps:

1. Toggle the feature flag off (if flag-gated).
2. Re-deploy the previous image tag.
3. If a destructive migration is involved (rare — should always be
   two-step), restore from backup per `22-backup-restore.md`.
4. Open an incident note; capture logs; runbook ref `24-runbooks.md`.

---

## 24. Examples of feature implementation

Each example is a **complete walkthrough** — class, mini-design
note, files, key code, tests, docs, rollout note, risks.

### 24.1 — Adding `/about` command

**Class:** F-Cmd. **Trivial.**

**Mini-design note (excerpt):**

```markdown
- Title: /about command
- Goal: show a static "about" message with bot version + author
- User story: As a USER, I want to see what this bot is, so that I know it's safe to use
- Class: F-Cmd
- Principles in play: P1, P4
- In-scope: static message; one button to /help
- Out-of-scope (explicit): bot statistics; user stats; admin info
- Impacted layers: bot, docs
- Layers explicitly NOT touched: application, domain, infrastructure, worker, deploy
- Data model impact: none
- Queue / worker impact: none
- Provider impact: none
- Delivery impact: none
- Config / env impact: none (BOT_VERSION read from existing setting)
- Logs: bot_about_invoked (INFO; user_id, chat_id)
- Tests: handler test with fake Update
- Docs: 06-bot-flow.md handler catalogue row
- Rollback: code revert; no data; safe instant
```

**Files (4):**

- `app/bot/handlers/about.py` (new)
- `app/bot/application_factory.py` (register)
- `app/tests/test_bot_about.py` (new)
- `docs/06-bot-flow.md` (handler catalogue row)

**Key code:**

```python
# app/bot/handlers/about.py
async def handle_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    bind_contextvars(
        request_id=str(uuid4()),
        user_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
    )
    _logger.info("bot_about_invoked")
    await update.message.reply_text(
        f"DWTGBot v{settings.bot_version}\n\nDownload media from supported platforms.",
        reply_markup=keyboards.about_keyboard(),
    )
```

**Tests:**

- `test_handle_about_renders_version_and_keyboard` — fake Update,
  assert reply text + keyboard structure.
- `test_handle_about_emits_log_event` — capture structlog output;
  assert event name + fields.

**Rollout note:** trivially safe; no flag gate.

**Risks:** none.

### 24.2 — Adding a new YouTube option (1440p bucket)

**Class:** F-Prov. **Small.**

**Mini-design note (excerpt):**

```markdown
- Title: 1440p YouTube bucket
- Goal: offer 1440p as a download option when the source has it
- User story: As a USER, I want a 1440p choice when YouTube provides it, so that I can pick higher quality
- Class: F-Prov
- Principles in play: P1, P4, P8
- In-scope: extend YouTube build_options to include 1440p when format is available
- Out-of-scope: 4K (separate PR); other providers
- Impacted layers: infrastructure (provider)
- Data model impact: none
- Queue / worker impact: none (existing options key space)
- Provider impact: BaseProvider unchanged; new DownloadOption.key "yt_1440p"
- Delivery impact: none
- Config impact: none
- Logs: existing youtube_options_built event extended with chosen formats
- Tests: provider test for 1440p-available, 1440p-unavailable, audio-only paths
- Docs: 07-provider-architecture.md provider table row
- Rollback: code revert; in-flight callbacks for older keys still work
```

**Files (4):**

- `app/infrastructure/providers/youtube/options.py` (modify)
- `app/tests/fixtures/yt_dlp_youtube_1440p.json` (new)
- `app/tests/test_providers_youtube.py` (extend)
- `docs/07-provider-architecture.md` (provider table)

**Tests:**

- `test_youtube_options_includes_1440p_when_available`
- `test_youtube_options_omits_1440p_when_unavailable`
- `test_youtube_audio_path_unchanged`

**Rollout note:** new key string; in-flight callbacks for older
keys (`yt_720p`, `yt_1080p`) keep working; no migration needed.

**Risks:** if a user's pending callback is for `yt_1440p` and the
build_options later returns it on a stream that doesn't actually
have it, we get a yt-dlp failure. Mitigation: validate option
availability in the use case before download (already done).

### 24.3 — Adding "download as zip" for Instagram gallery

**Class:** F-Prov + F-Queue + F-Cfg. **Medium. Multi-class →
split into three PRs.**

**Decomposition:**

- **PR1 (F-Prov):** Instagram provider returns a multi-file
  `DownloadResult` for galleries (already a list — extend to mark
  the bundle's intent).
- **PR2 (F-Queue):** new task `pack_zip` that consumes the multi-
  file result and emits a single zip; idempotent
  (`_job_id = f"zip:{job.id}"`); written to scratch dir; size
  threshold delegated to delivery use case.
- **PR3 (F-Cfg + F-Cmd):** new env var `ZIP_ENABLED=false`; new
  `/settings` flow that lets a user opt-in; UI text updated.

**Why split:** rollout independence (each phase ships and is
verified separately), reviewer cognitive load, P1/P3 compliance.

**Files (illustrative for PR2):**

- `app/workers/tasks/pack_zip.py` (new)
- `app/application/services/zip_packer.py` (protocol)
- `app/infrastructure/zip/runner.py` (zipfile-based impl)
- `app/workers/settings.py` (register task)
- `app/tests/test_pack_zip.py` (with fake packer)
- `docs/09-queue-and-workers.md` (task table row)
- `docs/14-logging-observability.md` (events)

**Key code (PR2):**

```python
# app/workers/tasks/pack_zip.py
async def pack_zip(ctx: dict, *, job_id: str) -> None:
    bind_contextvars(job_id=job_id)
    container = ctx["container"]
    use_case = container.pack_zip_use_case
    try:
        await use_case.execute(job_id=job_id)
    except RetryableError as exc:
        _logger.warning("pack_zip_retry", reason=exc.code)
        raise Retry(defer=ctx["job_try"] * 30)
```

**Tests:** fake packer; assert idempotency (running twice yields the
same zip path; second run is a no-op).

**Rollout note:** PR1 ships first (no observable effect); PR2 ships
behind `ZIP_ENABLED=false`; PR3 enables UI when flag is on.

**Risks:** large galleries inflate disk; cleanup task must purge
zips; flag-gated rollout.

### 24.4 — Adding expiry warning for temp links

**Class:** F-Deliv + F-Cmd. **Small.**

**Mini-design note (excerpt):**

```markdown
- Title: expiry warning when sending temp link
- Goal: tell user when their link expires
- User story: As a USER, I want to know when my download link will stop working, so that I don't lose access
- Class: F-Deliv + F-Cmd
- Principles in play: P4, P11
- In-scope: append "valid until <UTC>" line to the bot's reply when sending a temp link
- Out-of-scope: re-issuing expired links; renewal UX
- Impacted layers: bot (text rendering), application (fetch TTL from settings)
- Data model impact: none
- Queue / worker impact: none
- Provider impact: none
- Delivery impact: none (TTL logic unchanged; only displayed)
- Config impact: none (use existing TEMP_LINK_TTL setting)
- Logs: existing delivery_temp_link event extended with `expires_at`
- Tests: render test (handler output contains the timestamp); existing tests still green
- Docs: 10-temp-links-and-delivery.md (UI section)
- Rollback: code revert; pure presentation
```

**Files (3):**

- `app/bot/handlers/deliver.py` (modify text render)
- `app/tests/test_bot_deliver.py` (extend)
- `docs/10-temp-links-and-delivery.md` (UI behaviour)

**Risks:** none (pure presentation; no security or data impact).

### 24.5 — Adding new env-controlled behavior (`MAX_PARALLEL_DOWNLOADS`)

**Class:** F-Cfg + F-Queue. **Medium.**

**Mini-design note (excerpt):**

```markdown
- Title: configurable max parallel downloads
- Goal: cap concurrent downloads per worker process
- User story: As an OPS, I want to tune concurrency so that I can match my hardware
- Class: F-Cfg + F-Queue
- Principles in play: P4, P7, P9
- In-scope: read MAX_PARALLEL_DOWNLOADS from Settings; pass to arq WorkerSettings.max_jobs
- Out-of-scope: per-platform throttling; UI for the limit
- Impacted layers: config, worker
- Data model impact: none
- Queue impact: max_jobs becomes configurable; default preserves current value (4)
- Provider impact: none
- Delivery impact: none
- Config impact: NEW env var MAX_PARALLEL_DOWNLOADS — default 4 — added in five places
- Logs: worker_settings_loaded event emits the limit at startup
- Tests: Settings validation test (positive int, clamp to 1..32); worker startup test
- Docs: 13-config-and-env.md table row; 09-queue-and-workers.md (capacity note)
- Rollback: revert env var; default preserves old behaviour
```

**Files (5):**

- `app/config.py` (add field)
- `app/workers/settings.py` (use it)
- `.env.example`, `deploy/{single,nl1,nl2}/.env.example`,
  `deploy/compose/{control,media}.yml` (env passthrough)
- `app/tests/test_config.py` (extend)
- `docs/13-config-and-env.md` (row), `docs/09-queue-and-workers.md`
  (capacity note)

**Rollout note:** safe — default preserves current behaviour;
operator changes via `.env` and restarts worker (NL-2 в `split`).

**Risks:** operator sets a too-high value and exhausts disk /
memory. Mitigation: clamp validator + doc warning.

### 24.6 — Adding a new job status field (`delivered_at`)

**Class:** F-DB + F-Queue. **Medium. Two-step rollout.**

**Mini-design note (excerpt):**

```markdown
- Title: delivered_at on download_jobs
- Goal: record when a job was actually delivered (separate from completed_at)
- User story: As an OPS, I want to know when delivery happened, so that I can debug delivery latency
- Class: F-DB + F-Queue
- Principles in play: P4, P7, P10
- In-scope: new nullable timestamptz column; worker writes it on delivery
- Out-of-scope: UI for it; metrics; reads (later PR)
- Impacted layers: db, infrastructure (model + repo), worker
- Data model impact: additive nullable column; downgrade drops it
- Queue impact: worker writes the marker as part of the idempotency story
- Provider impact: none
- Delivery impact: none
- Config impact: none
- Logs: worker_delivered already exists; add delivered_at to the fields
- Tests: repo write test; worker idempotency test asserting marker is set once
- Docs: 12-db-schema.md (column row); 09-queue-and-workers.md (idempotency note updated)
- Rollback: code revert is safe (column stays nullable); migration downgrade drops the column
```

**Files (5):**

- `migrations/versions/0007_add_delivered_at.py` (new)
- `app/infrastructure/db/models.py` (modify)
- `app/infrastructure/db/repositories/jobs.py` (write path)
- `app/workers/tasks/deliver.py` (set marker before exit)
- `app/tests/test_jobs_repo.py` + `app/tests/test_deliver_task.py`
- `docs/12-db-schema.md`, `docs/09-queue-and-workers.md`

**Rollout note:** **deploy migration first**, then code that
writes to the new column. Two-step ensures no live old code
crashes on schema mismatch.

**Risks:** if migration applies but code deploy fails, the column
stays empty — safe (nullable). If code deploys before migration,
writes fail — mitigated by migration-first deploy order.

---

## 25. Quick reference

The fifteen daily rules. If you remember nothing else from this
guide, keep these.

1. **Every feature is classified (§4).** No class → no plan → no
   code.
2. **Every non-trivial feature emits a §7 mini-design note.**
   Verbatim. Before any file edit.
3. **`Out-of-scope` and `Layers explicitly NOT touched` are
   mandatory.** Empty = under-designed.
4. **Decide layer first (§8).** Domain → application → infrastructure
   → entry → composition. Inside-out.
5. **One PR = one feature class (§4).** Multi-class features split
   into staged PRs in dependency order.
6. **No business logic in handlers (P6).** Handlers route, period.
7. **Provider contracts are sacred (P8).** `BaseProvider` surface
   only; platform code stays inside the provider.
8. **Queue contracts are sacred (P7).** Deterministic `_job_id`,
   bounded `max_tries`, idempotent retries, stateless workers.
9. **Schema changes always migrate (P10).** Hand-reviewed; two-step
   for destructive; deploy migration first.
10. **Five-place rule for env vars (§15.4 / P4).** All five or none.
11. **Security defaults are floors (P11).** TLS, HSTS, `internal;`,
    `ensure_within`, `sanitize_filename`.
12. **Docs / tests / config travel with code (P4).** Same PR.
13. **Risky features ship behind a flag (§19.5).** Default off;
    enable for internal first; observe; then broadly.
14. **Every feature has a rollback story (§22.10).** Code revert +
    DB rollback + flag toggle.
15. **When in doubt, refuse and ask.** Use [`26-` §17.9](26-cursor-rules.md#179-refusal-scripts-verbatim--copy-fill--send)
    refusal scripts.

---

## Operational quick links

| Need | Place |
|---|---|
| Runnable preflight commands | §5.3 (this doc) |
| Class-picker grep recipe | §5.4 (this doc) |
| Runnable test commands | §17.6 (this doc) |
| BEFORE / AFTER code samples for feature anti-patterns | Appendix C (this doc) |
| BEFORE / AFTER code samples for provider anti-patterns | [`30-` Appendix D](30-add-new-provider-guide.md#appendix-d--before--after-code-samples-for-the-worst-anti-patterns) |
| Per-step provider verification | [`30-` §24 Step 11](30-add-new-provider-guide.md#step-11--per-step-verification-commands) |

---

## Appendix A — Pointer index

If you remember nothing else, remember **where to look**:

| Need | Place |
|---|---|
| What the eleven principles mean | [`25-` §1.A](25-agent-guide.md#1a-mandatory-principles-p1p11--canonical-register), [`26-` §1.A](26-cursor-rules.md#1a-mandatory-principles-p1p11--operational-register) |
| The verbatim plan template | [`25-` §6.A](25-agent-guide.md#6a-plan-template-verbatim--emit-before-any-file-edit), [`28-` §5.1](28-implementation-playbook.md#51-planning-note-template-verbatim--emit-before-any-file-edit) |
| The verbatim mini-design note | §7.1 (this doc) |
| What to do for a change of class X | [`28-` §§7–21](28-implementation-playbook.md) |
| Per-feature playbooks (when / pre-flight / DO-DON'T) | §§9–15 (this doc) |
| What to update alongside what | [`28-` §22](28-implementation-playbook.md#22-what-must-be-updated-together) + §18.1 (this doc) |
| What never to do | §§20–21 (this doc), [`28-` §§23–24](28-implementation-playbook.md) |
| Did I forget anything? | §§22–23 (this doc), [`28-` §§25 + 27](28-implementation-playbook.md) |
| Quick refusal scripts | [`26-` §17.9](26-cursor-rules.md#179-refusal-scripts-verbatim--copy-fill--send) |
| Code skeletons | [`28-` Appendix B](28-implementation-playbook.md#appendix-b--code-templates-copy-pasteable-skeletons) |
| Pre-merge mechanical sweep | [`28-` Appendix C](28-implementation-playbook.md#appendix-c--pre-merge-grep-recipes) |

---

## Appendix B — Mini-design note examples

Two filled-in examples (one F-Cmd, one F-DB+F-Queue) showing the
style and depth expected in §7.1 notes.

### B.1 — Filled note for §24.1 (`/about` command)

```markdown
### Feature mini-design note

- Title: /about command
- Goal: show a static "about" message with bot version + author
- User story: As a USER, I want to see what this bot is, so that I know it's safe to use
- Class (§4): F-Cmd
- Principles in play (§2): P1, P4

- In-scope:
  - Static message with bot version
  - Inline keyboard with one button → /help
- Out-of-scope (explicit):
  - Bot statistics
  - User stats
  - Admin info

- Impacted layers: bot, docs
- Layers explicitly NOT touched: application, domain, infrastructure, worker, deploy

- Data model impact: none
- Queue / worker impact: none
- Provider impact: none
- Delivery impact: none
- Config / env impact: none — uses existing BOT_VERSION setting

- Logs: bot_about_invoked (INFO; fields: user_id, chat_id, request_id)

- Tests:
  - Unit: test_bot_about.py — test_handle_about_renders_version_and_keyboard, test_handle_about_emits_log_event
  - Negative paths: none meaningful (no failure modes)

- Docs to update in this PR:
  - docs/06-bot-flow.md — handler catalogue row

- Risks / blast radius: none — pure presentation
- Rollback story: code revert; no data; instant
- Verification: local manual /about; assert log emission
```

### B.2 — Filled note for §24.6 (`delivered_at` column)

```markdown
### Feature mini-design note

- Title: delivered_at on download_jobs
- Goal: record when a job was actually delivered (separate from completed_at)
- User story: As OPS, I want to know when delivery happened so that I can debug delivery latency
- Class (§4): F-DB + F-Queue
- Principles in play (§2): P4, P7, P10

- In-scope:
  - New nullable timestamptz column delivered_at on download_jobs
  - Worker writes it as part of the existing idempotency story
- Out-of-scope (explicit):
  - UI / bot surface for it
  - Metrics export
  - Read paths (separate PR)

- Impacted layers: infrastructure (db model + repo), worker, docs
- Layers explicitly NOT touched: bot, application use cases, api, deploy

- Data model impact: additive nullable column; downgrade drops it
- Queue impact: worker idempotency marker; no _job_id change; no max_tries change
- Provider impact: none
- Delivery impact: none
- Config impact: none

- Logs: existing worker_delivered event extended with delivered_at field

- Tests:
  - Unit: jobs repo write test; deliver task idempotency test (marker set once)
  - Negative paths: marker set even on partial failure of optional follow-up steps

- Docs to update in this PR:
  - docs/12-db-schema.md — column row
  - docs/09-queue-and-workers.md — idempotency note updated

- Risks / blast radius:
  - Migration applied + code not deployed → column stays empty (safe; nullable)
  - Code deployed without migration → writes fail (mitigated by migration-first deploy order)
- Rollback story:
  - Code revert: safe (column stays nullable, no readers)
  - DB rollback: alembic downgrade drops the column
- Verification:
  - Local: alembic upgrade head; pytest; manual download → assert delivered_at populated
  - Production: log query for worker_delivered event; SELECT delivered_at FROM download_jobs LIMIT 5
```

---

## Appendix C — BEFORE / AFTER code samples for feature anti-patterns

For AI agents and engineers under deadline. Each block shows the
exact wrong code that has actually been written, the exact
replacement, and the principle violated. Match the symptom in
your diff; replace verbatim.

### C.1 — Business logic in a handler (P6)

```python
# BAD — handler decides job lifecycle, derives _job_id, talks to repo
async def handle_url(update, context):
    url = update.message.text
    platform = detect_platform(url)
    provider = REGISTRY.get(platform)
    info = await provider.get_info(url)
    job = DownloadJob(
        id=hashlib.sha256(f"{update.effective_user.id}:{url}".encode()).hexdigest(),
        user_id=update.effective_user.id,
        url=url, status=JobStatus.PENDING,
    )
    async with SessionLocal() as session:
        await JobsRepo(session).save(job)
    await arq_pool.enqueue_job("download", job.id)
    await update.message.reply_text(f"Поставлено в очередь, id={job.id}")
```

```python
# GOOD — handler binds context, calls one use case, replies
async def handle_url(update, context):
    bind_contextvars(
        request_id=str(uuid4()),
        user_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
    )
    container: BotContainer = context.application.bot_data["container"]
    try:
        info, options = await container.analyze_url_use_case.execute(
            url=update.message.text,
            user_id=update.effective_user.id,
        )
    except UserFacingError as exc:
        await update.message.reply_text(_user_message(exc.code))
        return
    await update.message.reply_text(
        _format_info(info),
        reply_markup=keyboards.options(info, options),
    )
```

**Why**: handlers must remain thin. Persistence, ID derivation,
and queueing live in the application layer, behind a use case
that is the single testable unit.

---

### C.2 — Hidden refactor inside a feature PR (P1, P3)

```diff
# BAD — adds /about command AND renames helpers AND restructures imports
+ app/bot/handlers/about.py            (new — feature work)
+ app/bot/keyboards/about.py           (new — feature work)
- app/bot/handlers/commands.py         (renamed greet() → say_hello(); illustrative)
- app/domain/text_utils.py             (moved helpers around; illustrative)
- app/application/services/queue.py    (added type hints unrelated to /about)
- 12 other unrelated files touched
```

```diff
# GOOD — feature PR is feature-only; rename is a separate PR
+ app/bot/handlers/about.py            (new)
+ app/bot/keyboards/about.py           (new)
+ app/tests/test_bot_about.py          (new)
+ docs/06-bot-flow.md              (about flow added)
+ docs/14-logging-observability.md     (bot_about_invoked event)
```

**Why**: a feature PR with hidden refactor is unreviewable. Open
a separate refactor PR; gate the feature on the refactor merging
first if needed.

---

### C.3 — Provider-specific branch outside the provider (P5, P6, P8)

```python
# BAD — gallery option chosen in a use case
async def execute(self, url: str, user_id: int) -> AnalyzeResult:
    platform = detect_platform(url)
    provider = self._registry.get(platform)
    info = await provider.get_info(url)
    options = provider.build_options(info)
    if platform is Platform.INSTAGRAM and len(info.items) > 1:
        options.append(
            DownloadOption(key="ig_zip", label="Скачать всё zip-ом",
                           kind=MediaKind.VIDEO, container="zip"),
        )
    return AnalyzeResult(info=info, options=options)
```

```python
# GOOD — option lives in InstagramProvider.build_options;
#        use case is platform-agnostic
async def execute(self, url: str, user_id: int) -> AnalyzeResult:
    platform = detect_platform(url)
    provider = self._registry.get(platform)
    info = await provider.get_info(url)
    return AnalyzeResult(info=info, options=provider.build_options(info))
```

See `30-` Appendix D.3 for the matching `InstagramProvider`
change.

**Why**: every `if platform == Platform.X` outside a provider is a
ratchet — the next platform adds another branch in the same place
until the use case is unreadable.

---

### C.4 — Job lifecycle changed by a feature (P7)

```python
# BAD — feature "warn user when temp link is about to expire" smuggles
#       a new job status and edits state machine in passing
class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED_SOON = "expired_soon"   # ← new, not in §12 of docs/12-

# In worker:
if job.status is JobStatus.COMPLETED and link.expires_at - now < 30*60:
    job.status = JobStatus.EXPIRED_SOON     # mutates terminal state
    await jobs_repo.save(job)
```

```python
# GOOD — terminal state is sacred; warning is a separate domain
#        concept (TempLinkWarning) with its own table or attribute
@dataclass(frozen=True)
class TempLinkWarning:
    job_id: str
    delivered_at: datetime
    expires_at: datetime
    notified_at: datetime | None

# In a separate scheduled task:
async def warn_expiring_links(ctx):
    use_case = ctx["container"].warn_expiring_links_use_case
    await use_case.execute(within=timedelta(minutes=30))
    # the use case never mutates JobStatus
```

**Why**: terminal job states are part of the queue contract.
Adding a new status (or worse, mutating a terminal one) breaks
every dashboard, alert, and assumption ops has built on the
state machine.

---

### C.5 — New env var added in only one place (P4)

```diff
# BAD — only .env.example is updated; production reads MAX_PARALLEL=None
+ MAX_PARALLEL_DOWNLOADS=4   # in .env.example only
```

```diff
# GOOD — five-place rule applied in the same PR
+ app/config.py                            : Settings.max_parallel_downloads: int = Field(default=4, ge=1, le=64)
+ .env.example                             : MAX_PARALLEL_DOWNLOADS=4
+ deploy/single/.env.example               : MAX_PARALLEL_DOWNLOADS=4
+ deploy/nl1/.env.example                  : MAX_PARALLEL_DOWNLOADS=4
+ deploy/nl2/.env.example                  : MAX_PARALLEL_DOWNLOADS=4
+ deploy/compose/control.yml               : environment block adds MAX_PARALLEL_DOWNLOADS
+ deploy/compose/media.yml                 : environment block adds MAX_PARALLEL_DOWNLOADS
+ docs/13-config-and-env.md                : new row with name/type/default/effect
+ app/tests/test_config.py                 : test that the field clamps + validates
```

**Why**: env vars not present in compose `environment:` are not
visible inside the container. "It works locally" is not a
deploy guarantee.

---

### C.6 — Tests deferred to a follow-up (P3, P4)

```diff
# BAD — feature ships green CI because no tests exist; coverage drops
+ app/bot/handlers/about.py            (new)
+ docs/06-bot-flow.md              (new flow)
# (tests "to follow")
```

```diff
# GOOD — feature PR carries its own tests; CI fails without them
+ app/bot/handlers/about.py
+ app/bot/keyboards/about.py
+ app/tests/test_bot_about.py          (renders text, calls keyboard, logs event)
+ docs/06-bot-flow.md
+ docs/14-logging-observability.md     (bot_about_invoked)
```

**Why**: "tests in a follow-up" is a euphemism for "tests never".
Same PR, no exceptions.

---

### C.7 — Data model change without thinking through deploy order (P9, P10)

```python
# BAD — non-nullable column with no default; migration breaks deploy
op.add_column(
    "download_jobs",
    sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
)
# during rolling restart: writers without the new column fail validation
```

```python
# GOOD — nullable now; backfill (if any) is a separate one-shot script;
#        promote to NOT NULL in a later PR if invariant holds
op.add_column(
    "download_jobs",
    sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
)
op.create_index(
    "ix_download_jobs_delivered_at",
    "download_jobs",
    ["delivered_at"],
)
```

**Why**: a feature PR must respect the `migration-first → code-second`
deploy order. NOT-NULL on a fresh column without a default
guarantees breakage during rolling deploy.

---

### C.8 — Logging without correlation context (P4)

```python
# BAD — no request_id / job_id; impossible to trace one user's flow
_logger.info("about command invoked")
```

```python
# GOOD — context bound at the entry, every log line carries it
bind_contextvars(
    request_id=str(uuid4()),
    user_id=update.effective_user.id,
    chat_id=update.effective_chat.id,
)
_logger.info("bot_about_invoked")
# downstream logs in this task automatically include request_id
```

**Why**: structured logs without correlation IDs are useless for
incident response. Bind once at the boundary; let `contextvars`
carry it.

