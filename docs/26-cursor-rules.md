# 26 — Cursor rules (operational protocol)

> Status: Stable
> Audience: Cursor (and any AI coding agent operating inside Cursor)
> Companion: [`25-agent-guide.md`](25-agent-guide.md) (the "why"),
> [`27-coding-standards.md`](27-coding-standards.md),
> [`28-implementation-playbook.md`](28-implementation-playbook.md)

> ⚠️ **This document is a protocol, not a tutorial.**
>
> `25-agent-guide.md` explains how to *think* about DWTGBot.
> **This** document tells Cursor what to *do* and *not do* — before,
> during, and after any change. If a section here looks bossy, that
> is the point. Cursor is expected to follow it like a pilot follows
> a checklist.
>
> Whenever this document and `25-agent-guide.md` disagree on the
> "why", `25-` wins. Whenever they disagree on the "what" of an
> operational step, **this document wins**.

> 🧭 **Document relationship map.** Most subsystem rules in this
> document are *operational projections* of explanations that live
> elsewhere. The canonical sources are listed in
> [Appendix C — Cross-reference & deduplication map](#appendix-c--cross-reference--deduplication-map)
> at the bottom of this file. **Before extending any section here,
> read that map** — if the topic has a canonical home in another
> document, the explanation goes there and only the Cursor-specific
> operational projection (one-liner prohibitions, refusal scripts,
> checklist gates) goes here. This is itself a P4 obligation: docs,
> tests, and config travel together — *and the duplication surface is
> bounded by design*.

---

## 1. Mission of Cursor in this project

### 1.1 What Cursor IS for

- Implementing changes that have a clear, narrowly-scoped goal.
- Carrying out the **safe change protocol** in
  [`25-agent-guide.md`](25-agent-guide.md) §6 step-by-step.
- Producing minimal, reviewable diffs.
- Updating tests, docs, and migrations in the **same change** as
  the code.
- Surfacing uncertainty explicitly when the request is ambiguous.

### 1.2 What Cursor is NOT for

- Re-architecting subsystems on its own initiative.
- Performing speculative refactors ("while I'm here…").
- Inventing new layers, new top-level modules, new services.
- Adding new dependencies without explicit user approval.
- Touching locked decisions without an ADR.
- Producing large PRs that mix unrelated concerns.
- Writing prose where a checklist or table would do.

### 1.3 Cursor's posture: conservative

In any judgement call, Cursor must default to the **smaller**,
**safer**, **more local** change. If two approaches are reasonable,
pick the one that touches fewer files, fewer layers, and fewer
locked decisions. **Conservative is correct.**

### 1.4 Authority

Cursor has **read** authority over the entire repository, and
**write** authority over code, tests, docs, and (with care)
deploy/scripts. Cursor does **not** have authority to:

- Change locked decisions (requires user + ADR).
- Add a new top-level dependency (requires user approval).
- Push to `main` without review.
- Run destructive operations on real infrastructure.
- Skip CI gates with `--no-verify`.

---

## 1.A Mandatory principles (P1–P11) — operational register

> **Canonical names & meanings:** [`25-agent-guide.md`](25-agent-guide.md) §1.A.
> **30-second lookup:** [`33-glossary.md`](33-glossary.md) → "Mandatory principles (P1–P11)".
> **This section** is the *operational projection* — a Cursor-specific "hard rule" per code, deliberately phrased as something a refusal script or pre-commit gate can name. If you tune the wording in `25-` or `33-`, mirror the change here in the same PR (P4 applies to its own register).

Cursor measures every change against the **eleven principles**
below. Codes (P1…P11) are stable and used throughout this file
(plan template, refusal scripts, anti-patterns, common-mistake
table, mid-edit signals, final checklist).

| Code | Principle | Hard rule for Cursor |
|---|---|---|
| **P1** | **Minimal safe scope** | Touch only files in §3.7 plan. `Files NOT touched` field is non-empty. Smaller diff always wins (§17.4 matrix). |
| **P2** | **Architecture first** | Layer + ADR-0005 §2 impact decided **before** opening a file. No code without §3.7 plan. |
| **P3** | **No unrelated changes** | Drive-by edits banned (§4.5). Refusal script B fires on bundled asks (§17.9.B). |
| **P4** | **Docs / tests / config travel together** | §15 mapping is the contract. §8.3 companion-artifacts checklist is mandatory. Refusal G blocks "skip the docs" / "skip the tests" (§17.9.G). |
| **P5** | **No infrastructure leakage into domain** | `app/domain/` imports stdlib only (§5.1). `app/application/` calls protocols, not impls (§5.1, §5.2). |
| **P6** | **No business logic in handlers** | Handler = parse → 1 use case → render. No DB / Redis / `yt-dlp` in `app/bot/handlers/*` or `app/api/*` (§5.2). |
| **P7** | **Queue semantics preserved** | `_job_id` deterministic; `max_tries` finite; idempotent side-effects; stateless workers (§12). |
| **P8** | **Provider contracts explicit** | Only `get_info` / `build_options` / `download` are public; all `yt-dlp` config inside the provider (§11.1, §11.2). |
| **P9** | **Deploy changes require idempotency reasoning** | Every script change passes §10.4 + idempotency re-run test. Compose changes follow §10.1 preservation rules. |
| **P10** | **DB changes require migration reasoning** | Every schema change has Alembic migration + deploy-order note + rollback thinking (§9). Editing shipped migrations forbidden (§16.20). |
| **P11** | **Security defaults never weakened** | §14.1 hard prohibitions are floors. TLS, HSTS, `internal` Nginx, `ensure_within`, `sanitize_filename`, secrets-via-`Settings` are not negotiable. |

### Where Cursor must reference these codes

- **Plan (§3.7)** — the `Principles in play` line lists the Ps
  the change touches; agent cannot proceed if it lists `none` for
  a non-trivial change.
- **Refusal scripts (§17.9)** — each script names the Ps it
  defends.
- **Anti-patterns (§16) and common mistakes (§16A)** — every row
  carries the violated P.
- **Mid-edit smell signals (§16B)** — each signal is tagged with
  the P it warns you about.
- **Final checklist (§19)** — closes with an explicit
  "P1–P11 compliance" gate.
- **Golden rules (§20)** — built around the eleven Ps.

If a change has no clear mapping onto P1–P11, **stop**. Either
the change is not yet understood, or the codebase doesn't have a
home for it (which means an ADR is required, not code).

---

## 2. Mandatory startup protocol — before any code change

Before writing **any** code, Cursor must complete steps 1–6 below.
This is non-negotiable.

| # | Step | Output |
|---|---|---|
| 1 | Read [`docs/README.md`](README.md) | confirmation that index hasn't changed substantially |
| 2 | Read [`docs/00-overview.md`](00-overview.md) | refreshed mental model |
| 3 | Read [`docs/02-architecture.md`](02-architecture.md) | layers + dependency rules in mind |
| 4 | Read [`docs/03-project-structure.md`](03-project-structure.md) | dependency matrix in mind |
| 5 | Read [`docs/25-agent-guide.md`](25-agent-guide.md) | rules of engagement |
| 6 | Read this file (`docs/26-cursor-rules.md`) | operational protocol |
| 7 | Read the **topic-specific** doc(s) per the task ([§4.2 in 25-](25-agent-guide.md#42-topic-specific-lookup)) | local context for the change |
| 8 | Read [**ADR-0005**](adr/0005-locked-architectural-assumptions.md) §2 (12-row register) and any other ADR touching the area | locked decisions awareness — non-skippable |

If any required doc is missing or empty, **stop** and tell the
user. Do not proceed without context.

> If the user's request is so trivial that this seems excessive
> (e.g. a typo fix), Cursor still reads steps 1–6 in skim mode.
> The cost is seconds; the benefit is consistency.

---

## 3. Mandatory planning protocol

Before touching any file, Cursor produces a short plan with these
six items:

1. **Goal** — one sentence. What does the system do after this
   change that it didn't before?
2. **Class of change** — pick from
   [`25-agent-guide.md`](25-agent-guide.md) §5 taxonomy
   (A–K).
3. **Files to add / modify / delete** — explicit list in expected
   order of edits.
4. **Files / layers explicitly NOT touched** — the negative space.
   This is what stops drive-by edits.
5. **Risks** — what could break? what is the blast radius?
6. **Verification** — what will Cursor run / inspect to confirm
   correctness (tests, linters, manual smoke, doc grep)?

If any of those is missing or hand-wavy, refine the plan before
coding. If after refinement the plan still has unresolved items,
**stop and ask** (see §17).

### 3.7 Plan output template (verbatim)

Cursor must emit the plan in this exact structure **before any
file edit**. A reviewer should be able to copy-paste it into the
PR description without reformatting.

```markdown
### Plan

- **Goal:** <one sentence>
- **Class:** <A | B | C | D | E | F | G | H | I | J | K> (per 25- §5)
- **Principles in play (§1.A):** <P1, P3, P4, … — list every P this change touches; "none" only for pure typo fixes>
- **ADR-0005 §2 row touched?** <No | Yes — row N → STOP, ADR first> (P2)
- **Reading done:** README, 00, 02, 03, 25, 26, ADR-0005 §2, <topic doc(s)>
- **Files to ADD:**
  - `path/to/new_file.py` — <why>
- **Files to MODIFY:**
  - `path/to/existing.py` — <one-line diff intent>
- **Files to DELETE:** <none | path — why>
- **Files / layers explicitly NOT touched:** (P1 + P3 enforcement)
  - <e.g. `app/bot/handlers/` — this is a use-case-only change>
- **Migration?** (P10) <No | Yes — `migrations/versions/NNNN_<slug>.py`, deploy order: …>
- **Env var?** (P4) <No | Yes — `<NAME>` added in: config.py, .env.example, deploy/{single,nl1,nl2}/.env.example, compose env block, 13- doc>
- **Tests:** (P4) <new test file(s) + which conditions; existing tests expected to stay green>
- **Docs to update in this PR:** (P4) <list of `docs/*.md` per 26- §15>
- **Queue / worker contract impact?** (P7) <No | Yes — what changes in `_job_id`, retries, status, idempotency>
- **Provider contract impact?** (P8) <No | Yes — which `BaseProvider` method, surface stability>
- **Deploy / script idempotency note?** (P9) <N/A | how re-running stays safe; what destructive op is gated by `confirm`>
- **Security defaults touched?** (P11) <No | Yes — what is weakened/strengthened, with justification>
- **Risks / blast radius:** <what could break; who else's code paths run through this; rollback story>
- **Verification:** <commands to run + manual smoke if any>
```

Rules about the plan:

- **No prose answers.** The plan is bullets and lists.
- **No "TBD" entries.** Every bullet has a concrete value or
  `none`. `TBD` means "I don't know yet" → stop, read more, ask.
- **No "etc."** Enumerate or say "none".
- **`Principles in play` is mandatory.** Empty list (for anything
  beyond a typo) means the change has not been classified — refuse
  to start (P1, P2).
- If `Files to MODIFY` exceeds 5 entries, **the change is not
  small** (P1): re-classify per §6 vs §7 rules.
- If `Files / layers explicitly NOT touched` is empty, the agent
  hasn't thought about scope (P1, P3). Refuse to start coding.
- If `Migration?` is `Yes` without a `deploy order` line → P10
  violation; fix it.
- If `Queue / worker contract impact?` is `Yes` without an
  idempotency story → P7 violation; fix it.
- If `Security defaults touched?` is `Yes` and the justification
  cell weakens a default without an ADR → P11 violation; refuse
  and surface to user.

### 3.8 Red-flag phrases in user requests (auto-detect)

When the user request contains any of the phrases below, Cursor
must **pause** and react per the right column **before** starting
to plan. These phrases routinely smuggle in scope creep, locked-decision
violations, or implicit refactors.

| Trigger phrase (RU / EN) | Why it's a red flag | Cursor's required action |
|---|---|---|
| "просто", "по-быстрому", "за пару минут", "just", "quickly", "real quick" | scope underestimate; usually 5–10 files | classify properly per §5/§6/§7 of 25-; do not assume "small" |
| "заодно", "while you're here", "пока ты там", "while at it" | invitation to drive-by edits | **refuse**; surface the additional ask as a separate task |
| "временно", "пока что так", "for now", "temporary fix" | tech debt with no owner | require a tracked TODO with owner + issue link, or refuse |
| "почисти", "приведи в порядок", "refactor this", "clean up", "modernize" | speculative refactor request | ask for an explicit goal; refuse "while-here" cleanups |
| "перепиши", "rewrite this", "make it nicer" | broad rewrite request | force a written design note before coding (§7.1) |
| "поменяй базу / очередь / логирование на X" | locked-decision change | check ADR-0005 §2; if locked → require superseding ADR before any code |
| "выкини миграцию", "забей на миграцию", "skip the migration" | schema-drift recipe | refuse; migrations are mandatory for any schema change |
| "не пиши тесты", "skip tests for now" | quality regression | refuse; tests travel with code (§8.3) |
| "лог покажи как-нибудь", "просто print" | logging contract violation | refuse; use `_logger` with structured fields per §13 |
| "захардкодь", "вставь токен", "put the secret in the code" | secret leak | **hard refuse**; secrets via `Settings` only |
| "сделай как на проде / на сервере", "match what's in production" | drift between repo and live | refuse to mirror live drift; either fix the repo + redeploy, or open an incident ADR |
| "обновлю env прямо на сервере", "edit `.env` on the host" | config drift | refuse; PR the change, then redeploy |
| "и заодно поправь N других мест" | bundling unrelated work | split into separate PRs; refuse to bundle |
| "не читай документацию, и так понятно" | skip startup protocol | refuse; §2 is non-negotiable |
| "выпусти прямо в main", "merge без ревью" | bypass review | refuse; §1.4 forbids it |

If a request triggers two or more red flags, Cursor must reply with a
refusal/clarification using a script from §17.9 — **before** producing
a plan.

---

## 4. File modification discipline

### 4.1 Scope discipline

| Allowed | Forbidden |
|---|---|
| Edit only the files in §3 step 3 | Touching files not in the plan |
| Add files in the layer the plan declared | Adding files in a different layer "while at it" |
| Adjust adjacent imports if the change demands it | Re-ordering / re-formatting unrelated imports |
| Fix a typo discovered in a file you're already editing | Hunting for typos elsewhere in the repo |

### 4.2 Formatting discipline

- **Do not run `ruff format` over files you didn't substantively
  edit.** Format only the files you changed.
- **Do not change line endings, trailing newlines, or quote style**
  in files you don't otherwise edit.
- **Do not "improve" indentation** in unrelated functions.

### 4.3 Renaming discipline

- Do not rename modules, classes, functions, or files unless the
  user explicitly asks, **or** the rename is required by the
  change.
- If a rename is required, do it as the **first commit** in a PR,
  isolated, with no behavior change. Then implement the behavior
  change in a second commit.

### 4.4 Deletion discipline

- Never delete a file or directory the user did not authorise.
- Never delete a doc unless explicitly asked. (To remove an ADR,
  set `Status: Superseded by ADR-XXXX` instead.)
- Never delete a migration. If a migration was wrong, write a new
  migration that fixes it.

### 4.5 Drive-by edit ban

A "drive-by" edit is any change to a file outside the planned
scope. Cursor is **prohibited** from drive-by edits. If a drive-by
seems necessary, surface it as a follow-up TODO with an owner —
do not silently include it.

---

## 5. Architecture preservation rules

These are direct prohibitions. Each one is enforceable by code
review.

> 🔒 **The foundation Cursor must preserve at all times** is enumerated
> in [**ADR-0005 — Locked architectural assumptions**](adr/0005-locked-architectural-assumptions.md)
> §2 (twelve rows: Python 3.14+ (ADR-0009), topology + plane composition
> (rows 2–4, superseded by ADR-0011: `single` | `split`), provider-based design, Redis queue, Postgres SoT,
> temp links, structured logging, Docker-first, GitHub Actions, bash
> installer). A change touching any row → **stop, draft a superseding
> ADR, surface to user**. Do not write the application code first.

### 5.1 Layer prohibitions

| You shall NOT | Reason |
|---|---|
| Import `app/infrastructure/*` from `app/domain/*` | domain must stay framework-free |
| Import `app/infrastructure/*` from `app/application/*` | application talks to protocols, not impls |
| Import `app/bot/*`, `app/api/*`, `app/workers/*` from `app/application/*` | application doesn't know transports |
| Import `sqlalchemy`, `arq`, `redis`, `telegram` from `app/domain/*` | non-stdlib in domain is forbidden |
| Import `os.environ` from anywhere outside `app/config.py` | config goes through `Settings` |
| Call `subprocess.run(..., shell=True)` anywhere | use `create_subprocess_exec(arg, arg)` |
| Call `time.sleep` in async code | use `await asyncio.sleep` |
| Use `print()` for output / debugging | use `_logger.<level>(...)` |
| Catch `Exception` to "make tests pass" | use the three-tier pattern |
| Skip `ensure_within` on a path derived from input | path traversal vulnerability |
| Skip `sanitize_filename` on a candidate filename | injection / shell expansion risk |
| Stream files via `FileResponse` for the public surface | must use `X-Accel-Redirect` |
| Add `subprocess` invocations outside `YtDlpRunner` / `FFmpegRunner` | sandboxes the external binaries |
| Mix Telegram presentation logic with provider logic | violates the provider contract |
| Write god-functions (>100 lines, >5 responsibilities) | refactor into focused units |
| Bypass `composition.py` for DI | wiring must remain centralised |

### 5.2 Boundary discipline

- **Bot handlers** call exactly **one** use case method per intent.
  No DB. No Redis. No `yt-dlp`.
- **Use cases** call protocols, never concrete classes from
  `infrastructure`.
- **Repositories** return **domain** entities, never ORM models.
- **Providers** return `MediaInfo` / `DownloadOption` /
  `DownloadResult`. Nothing platform-specific leaks out.
- **Workers** receive `ctx` and a job_id; they fetch the rest.

### 5.3 Inventing layers is forbidden

Do not introduce new top-level packages under `app/` (e.g.
`app/services/`, `app/helpers/`, `app/core/`). The layering is
fixed. If a new home seems needed, the change is architecturally
significant — open an ADR.

---

## 6. Rules for making small changes (≤ 3 files, single concern)

A "small change" is bounded by:
- ≤ 3 files modified;
- one layer touched;
- no schema, no public-surface change;
- no new dependency.

### 6.1 Process

1. State the goal in one sentence.
2. Identify the file(s) to change and the test file(s) to update.
3. Make the minimum change.
4. Update or add the test(s).
5. Update the relevant doc(s) per [§13.1 in 25-](25-agent-guide.md#131-what-must-be-updated-together).
6. Run linters + tests.
7. Self-review against the §8 checklist.

### 6.2 Constraints

- **No optional cleanup** in the same PR. Note it as a follow-up.
- **No "improvements" to neighbouring code.**
- **No new abstractions** ("I noticed this pattern repeats…").
- The diff should be small enough that a reviewer can read it in
  under 5 minutes.

### 6.3 Anti-patterns

| Smell | Action |
|---|---|
| You're touching > 3 files | reclassify as medium/large (see §7) |
| You're tempted to extract a helper | only if it's used in this PR; otherwise defer |
| You're tempted to rename | move to a separate isolated PR |
| You're updating > 1 layer | re-examine: are you sure this is small? |

---

## 7. Rules for making medium / large changes

A "medium / large" change is anything beyond the §6 bounds: > 3
files, multiple layers, schema change, new public surface, new
dependency, etc.

### 7.1 Required artifacts (in order)

1. **Design note** in the PR description (or a small temp file
   under `docs/working/` if the PR is huge). 5–15 bullets.
   Sections: goal, options considered, decision, trade-offs,
   migration path.
2. **Impacted-files map** — table of files to add/modify/delete,
   grouped by layer, with one-line per-file reason.
3. **Staged implementation plan** — split the change into
   reviewable commits:
   - commit 1: refactor / extract (no behavior change)
   - commit 2: add new code paths (dormant)
   - commit 3: switch over
   - commit 4: remove old code
4. **Test plan** — what unit tests, what integration smoke, what
   manual verification.
5. **Docs plan** — which docs change in this PR, which ADRs
   (if any).

### 7.2 Process

1. Write the design note. Get user feedback before coding.
2. Implement layer-by-layer, **inside-out** (domain → application
   → infrastructure → entry layer → composition → tests →
   docs).
3. After each layer, run linters + tests for the touched files.
4. Final pass: review the **diff** as a stranger would. Remove
   anything that doesn't belong.
5. PR description includes design note, files map, test plan, docs
   plan, risk, rollback.

### 7.3 Hard limits

- **No PR exceeds 1000 lines of substantive change** (excluding
  generated migrations, lockfiles, formatted long tables). If it
  does, split it.
- **Every PR is reviewable end-to-end in 30 minutes by an unfamiliar
  reviewer.** If yours isn't, restructure.
- **Every commit is independently green** (lint + tests).

---

## 8. Mandatory pre-commit checklist

Before declaring a change ready, Cursor confirms each of these:

### 8.1 Gates

- [ ] Plan from §3 still matches the actual diff.
- [ ] Files modified == files in the plan. Nothing more.
- [ ] No drive-by edits.
- [ ] No unrelated formatting churn.
- [ ] No commented-out code blocks left behind.
- [ ] No new TODOs without an owner (`TODO(name): why`).
- [ ] No `print(...)` left in.
- [ ] No `time.sleep` in async code.
- [ ] No `subprocess.run(..., shell=True)`.
- [ ] No bare `except`.
- [ ] No `os.environ[...]` outside `app/config.py`.

### 8.2 Quality

- [ ] `ruff format .` clean (only on changed files).
- [ ] `ruff check .` clean.
- [ ] `mypy app` clean (or only pre-existing failures, called out).
- [ ] `pytest -m "not integration"` green.
- [ ] If shell touched: `bash -n` + `shellcheck` clean.

### 8.3 Companion artifacts

- [ ] Tests added or updated.
- [ ] Docs updated per [§13.1 in 25-](25-agent-guide.md#131-what-must-be-updated-together).
- [ ] If new env var: `.env.example` + per-stack `.env.example` +
      compose env block + `13-config-and-env.md` updated.
- [ ] If schema change: alembic migration + model + repo + doc
      `12-db-schema.md`.
- [ ] If new container service: `restart`, `healthcheck`,
      `logging`, `network` set.
- [ ] If new public route: rate limit + auth considered.
- [ ] If install script changed: idempotency verified by re-run.

### 8.4 Safety

- [ ] No secret in the diff.
- [ ] No new public-port exposure on NL-1.
- [ ] No path-traversal-friendly code path (every path through
      `ensure_within`).
- [ ] No PII / token / cookie in logs.
- [ ] No `chmod 777`, no `rm -rf $UNSET_VAR`.

If a single box can't be ticked, **fix it before submitting**.

---

## 9. Rules for migrations

### 9.1 When a migration is required

A migration is required for any change to PostgreSQL schema:
columns, tables, indexes, constraints, enum values, types,
defaults, unique-key composition. ORM-only changes (e.g. adding a
`__repr__`) do not need one.

### 9.2 How to author one

1. `alembic revision --autogenerate -m "<short, imperative msg>"`.
2. **Open the generated file and review it by hand.** Autogen
   misses: ENUM additions, renames (treats as drop+create), data
   backfill needs.
3. Edit `upgrade()` to:
   - add explicit `op.execute("ALTER TYPE … ADD VALUE …")` for
     enums;
   - add backfill SQL guarded by `if conn.dialect.name == "postgresql"`;
   - prefer `op.batch_alter_table(...)` for column ops.
4. Provide a `downgrade()` body or document explicitly why it is
   a no-op.
5. Test:
   - `alembic upgrade head` on a fresh DB.
   - `alembic downgrade -1 && alembic upgrade head` clean.

### 9.3 Destructive change rules

- **Don't drop columns** in the same migration that stops writing
  to them. Two-step: stop writing → next release → drop in a
  follow-up migration.
- **Don't rename columns**. Use add → dual-write → backfill →
  drop old over multiple releases.
- **Don't add a NOT NULL column without a default** to a populated
  table. Always add nullable + default first; backfill; then add
  NOT NULL constraint.

### 9.4 Companion updates

| You wrote | You also update |
|---|---|
| New migration | `app/infrastructure/db/models.py` |
| New column | repository impl that reads/writes it |
| New domain field | use cases that touch it; tests |
| Any of the above | doc `12-db-schema.md` |
| Enum addition | every `match`/branch on the enum |

### 9.5 Deploy order

For any migration, answer in the PR description: **what is the safe
deploy order?** The default safe order is:

1. Apply migration (compatible with both old and new code).
2. Deploy new code.
3. Optionally clean up the old schema in a follow-up migration.

If the migration is *not* compatible with old code, that is a
high-risk change and **requires user approval** before merging.

---

## 10. Rules for deploy / config changes

### 10.1 Compose changes

Any change to the compose fragments (`deploy/compose/{control,media}.yml`),
the topology overlays (`deploy/single/single.override.yml`,
`deploy/nl1/nl1.overlay.yml`) or the stack entrypoints
(`deploy/{single,nl1,nl2}/docker-compose.yml`, только `include`) must:

- Keep services in the fragments; stacks never redefine a service.
- Pass `app/tests/test_deploy_topology.py` and `docker compose config -q`
  for all three stacks (CI job `compose-validate`).

- Preserve YAML anchors (`x-app-env`, `x-logging`, etc.).
- Preserve `restart: unless-stopped` on every service.
- Preserve `healthcheck` blocks (or add them for new services).
- Preserve named networks and volumes.
- Pass through env vars consistently with the plane's `.env`
  template.
- Not expose new ports on NL-1.
- Not co-locate `postgres` / `redis` on NL-2.
- Be accompanied by a one-line "why" in the PR description.

### 10.2 Nginx changes

Any change to `deploy/nginx/*` must:

- Preserve `internal;` on the storage `location`.
- Preserve `limit_req_zone` on public-facing locations.
- Preserve security headers (HSTS, X-Frame-Options, etc.) listed
  in `deploy/nginx/snippets/security.conf`.
- Be accompanied by an explanation of the request flow it changes.
- Be tested with `nginx -t` inside the container.

### 10.3 Certbot changes

Any change to `deploy/certbot/*` or `deploy/scripts/certbot_init.sh`
must explain:

- How automatic renewal still works.
- What happens to in-flight downloads during a renewal restart
  (they should not break — Nginx reloads gracefully).

### 10.4 Install / operator script changes

Any change to `deploy/scripts/*.sh` must:

- Begin with `set -Eeuo pipefail`.
- Be **idempotent**: re-running on a healthy system is a no-op.
- Prompt with `confirm "..." "N"` for destructive operations.
- Pass `bash -n` and `shellcheck --severity=warning`.
- Use logging helpers (`log_info`, `log_warn`, `log_step`) — no
  raw `echo`.
- Preserve the OS target check (Ubuntu 24.04 LTS).
- Be tested by re-running on a system where it has already been
  run.

### 10.5 Env var changes

Adding or changing an env var requires updates in **all** of:

- `app/config.py` (the typed field with validation).
- `.env.example` (root template).
- `deploy/nl1/.env.example` and/or `deploy/nl2/.env.example` (per
  plane).
- The relevant `docker-compose.yml` `environment:` block.
- `docs/13-config-and-env.md` (variable reference + checklist).
- (If the var is a secret) `deploy/scripts/install.sh` generation
  logic.

Forgetting any one of these causes silent prod misconfiguration.

### 10.6 CI/CD changes

Any change to `.github/workflows/*.yml` must:

- Preserve required gates (lint, mypy, tests, shellcheck).
- Preserve image tagging policy.
- Not introduce new secrets without user approval.
- Not skip caching (build performance regressions matter).
- Be documented in `docs/21-cicd.md` if behaviour changes.

---

## 11. Rules for providers and media pipeline

### 11.1 Provider contract preservation

- Implement `BaseProvider` exactly. Do not add public methods that
  application or worker code is expected to call — application
  code talks to the **protocol**, not the class.
- `get_info` is async; `build_options` is pure; `download` is
  async.
- All `yt-dlp` and platform-specific config lives **inside the
  provider class**.

### 11.2 Trusting provider output

Treat yt-dlp output as **untrusted, schema-drifting, and
optional**. Specifically:

- Do not assume keys exist. Use `.get()` with defaults.
- Do not assume `formats` is non-empty. Validate.
- Do not assume titles / filenames are safe. Always
  `sanitize_filename`.
- Do not assume sizes are accurate. Re-stat after download.
- Do not assume URLs in metadata are safe to log.

### 11.3 File lifecycle in providers

- Every file written must be inside `target_dir`.
- Use `ensure_within(STORAGE_PATH, candidate)` if computing paths.
- Set permissions explicitly (do not rely on umask).
- Log file count and bytes after writing (event:
  `<platform>_download_done`).

### 11.4 Failure classification

Classify provider/download failures into:

| Class | Example | Behavior |
|---|---|---|
| **Retryable transient** | network glitch, 5xx from yt-dlp | raise → arq retries with backoff |
| **Retryable rate limit** | 429 | raise → arq retries with longer backoff |
| **Permanent (user-meaningful)** | URL not supported, geo block, age-gated | raise `DownloadError` with `user_message`; arq does NOT retry past `max_tries` |
| **Permanent (programmer error)** | code bug, type error | `_logger.exception(...)` → arq fails the job |

Do not collapse these into one "retry forever" loop.

### 11.5 Cleanup discipline

- Half-downloaded files in scratch dirs are the cleanup worker's
  responsibility — but the provider must not leave files outside
  scratch dirs.
- On exception, re-raise after logging; let the worker's
  exception path handle the DB row update.

---

## 12. Rules for queue and worker code

### 12.1 Idempotency

- The arq `_job_id` for a logical request is **deterministic** —
  derived from `job:{download_jobs.id}` (or equivalent). Don't add
  randomness.
- A retry of the same job must produce the same observable
  outcome (file delivered, status `success`, no double Telegram
  message).
- Sender writes a "delivered" marker (DB row update) **before**
  the task exits successfully; subsequent retries detect it and
  short-circuit.

### 12.2 Status transitions

Job status transitions (`pending → in_progress → success | failed
| partial_success`) are explicit and logged. Don't update the row
silently. Use the repository methods that emit log events.

### 12.3 Retries

- `max_tries` is set per task type; **never `None`**.
- `retry_delay` uses exponential backoff for transient failures.
- Permanent errors (`DownloadError` with non-retryable kind)
  short-circuit retries.

### 12.4 Duplicate sends

If retry happens after partial success (Telegram upload completed
but the task crashed before marking `success`), the task must:
- detect it (delivery marker in DB);
- not re-upload;
- mark status `success` and exit cleanly.

This requires writing the marker **as soon as** the user-visible
side effect completes.

### 12.5 Partial completion

If a download produces some files before failing:

1. Either: commit them (mark `partial_success`, deliver what
   exists, log `delivery_partial`).
2. Or: roll them back (delete scratch dir, mark `failed`, raise).

Choose one explicitly per task; do not leave dangling state.

### 12.6 Logging

Every task logs:

- `worker_pick_job` at start (with `job_id`, `try_count`).
- `worker_finish_job` at successful end.
- `worker_job_failed` on failure (with `error_kind`).
- Plus dependency events (provider, ffmpeg, sender) per the
  event catalogue in [`14-logging-observability.md`](14-logging-observability.md).

---

## 13. Logging rules for Cursor-authored code

### 13.1 Setup

- Module-level logger: `_logger = get_logger(__name__)`.
- Bind correlation context at the top of every async unit of
  work:

  ```python
  with bind_context(request_id=req_id, job_id=job.id, user_id=uid, chat_id=cid):
      ...
  ```

### 13.2 Style

- Event names: `snake_case`, **constant strings**, never f-strings.
- Structured fields, not interpolated strings:
  ```python
  _logger.info("download_pipeline_done", job_id=job.id, bytes=size)
  ```
- Levels:
  - **DEBUG** — internal traces.
  - **INFO** — state transitions, decisions.
  - **WARNING** — handled errors that affect the user.
  - **ERROR** — unhandled or programmer-error.
  - **CRITICAL** — service-wide failure (rare).
- Use `_logger.exception(...)` (not `_logger.error(...)`) when an
  exception object is in scope.

### 13.3 Forbidden in logs

- Full URLs that may contain tokens / cookies.
- The `BOT_TOKEN`, DB password, Redis password, any secret.
- Authorization headers, cookies, session ids.
- Full temp-link tokens (debug may use last 4 chars only).
- Arbitrary user message bodies (might contain sensitive info).

### 13.4 Internal vs user-facing errors

- **User messages** come from `AppError.user_message` (a friendly
  string).
- **Internal logs** carry the full context: exception type, raw
  message, structured fields.
- **Never** show the raw exception string to the user. Never
  surface stack traces to the user.

### 13.5 New event names

Every new event name added to the codebase must also be added to
the catalogue in
[`14-logging-observability.md`](14-logging-observability.md) §4.

---

## 14. Security rules

### 14.1 Hard prohibitions

| You shall NOT | Reason |
|---|---|
| Add `subprocess.run(..., shell=True)` | shell injection vulnerability |
| Concatenate user input into a path | path traversal |
| F-string user input into SQL | SQL injection |
| Skip `ensure_within(STORAGE_PATH, ...)` for any path | path traversal |
| Skip `sanitize_filename` for any candidate filename | shell expansion / writes outside target |
| Log a token, secret, cookie, or Authorization header | credential leak |
| Embed secrets in code, configs, or docs | leak via VCS |
| Open public ports on NL-1 | breaks the security perimeter (ADR-0001) |
| Make Nginx storage `location` non-`internal` | bypasses token validation |
| Stream public files via `FileResponse` | bypasses Nginx + ties workers |
| Loosen TLS / HSTS / firewall defaults | downgrade attack risk |

### 14.2 Validation discipline

- Validate every URL with `app/utils/url.py::detect(...)`.
- Validate every callback data string against the codec.
- Validate every public HTTP input with FastAPI's typed schemas.
- Treat every yt-dlp / Telegram payload as untrusted.

### 14.3 Defaults

When you have a choice:
- Smaller TTL > larger TTL.
- Lower max-downloads > higher.
- Tighter rate limit > looser.
- Internal binding > public binding.
- Stricter file mode > looser.

If you weaken any of these, the PR description must justify it.

### 14.4 New surfaces

If you add anything reachable from the Internet (new endpoint, new
header, new query param):

1. Add rate limiting at Nginx level.
2. Validate inputs at the boundary; trust nothing.
3. Document the change in [`17-security.md`](17-security.md).
4. Consider whether it changes the threat model — if yes, **ADR
   required**.

---

## 15. Documentation update rules

For each kind of change, the matching docs **must** be updated in
the same PR. This is enforced by code review.

| If you change… | Update these docs |
|---|---|
| Architecture / layering | `02-architecture.md`, `03-project-structure.md`, possibly an ADR |
| Domain entities | `04-domain-model.md` |
| Bot UX | `06-bot-flow.md` |
| Provider | `07-provider-architecture.md` |
| Download pipeline | `08-download-pipeline.md` |
| Queue / worker | `09-queue-and-workers.md` |
| Public delivery / Nginx | `10-temp-links-and-delivery.md` |
| Storage layout / cleanup behavior | `11-storage-strategy.md`, `23-cleanup-retention.md` |
| DB schema | `12-db-schema.md` |
| Config / env var | `13-config-and-env.md` |
| Logging | `14-logging-observability.md` |
| Healthchecks | `15-healthchecks.md` |
| Errors | `16-error-handling.md` |
| Security surface | `17-security.md` |
| Tests strategy | `18-testing-strategy.md` |
| Docker images | `19-docker-architecture.md` |
| Deploy procedure | `20-deployment.md` |
| CI/CD | `21-cicd.md` |
| Backup behavior | `22-backup-restore.md` |
| Cleanup behavior | `23-cleanup-retention.md` |
| New incident pattern | `24-runbooks.md` |
| Coding rule | `27-coding-standards.md` |
| Workflow / agent rule | `25-agent-guide.md` and this file |
| New term used in 3+ docs | `33-glossary.md` |
| Locked decision | new ADR + every doc that referenced the old decision |

If a doc update is unclear, **err on the side of updating**. A
slightly redundant note is better than a stale spec.

Правило языка документации: все новые и изменяемые описания документации
пишите на русском языке. Технические идентификаторы, команды, env vars,
пути, API routes, event names и названия библиотек оставляйте без
перевода, чтобы ссылки, команды и поиск по коду оставались стабильными.

---

## 16. Anti-patterns specific to Cursor

These are patterns observed when AI agents — including Cursor —
overstep. Each is **prohibited**.

### 16.1 Broad rewrites

❌ Rewriting an entire module to "modernise" it while addressing a
small bug.
✅ Make the smallest change that fixes the bug. Note the
modernisation as a follow-up.

### 16.2 Speculative refactors

❌ Extracting helpers, renaming variables, restructuring
conditionals "while I'm here".
✅ Touch only what the plan requires.

### 16.3 Silent architecture drift

❌ Moving logic from `application/` to `infrastructure/` (or vice
versa) without an ADR or design note.
✅ Architecture moves are explicit, documented, and reviewed.

### 16.4 Bloated diffs

❌ Changing 20 files for 1 small bug.
✅ If the diff sprawls, stop and re-plan.

### 16.5 Unrequested dependencies

❌ Adding a new library because it would "be cleaner".
✅ Use the existing libraries. New deps require user approval.

### 16.6 Casual layer movement

❌ "I'll just put this helper in `app/domain/` for now."
✅ Place code where it belongs by the layer rules. If unsure, ask.

### 16.7 TODOs instead of finishing

❌ `# TODO: handle this case later`.
✅ Either handle it now (and add tests) or surface it explicitly
to the user as a follow-up.

### 16.8 Placeholder docs

❌ `## Section to be filled in later.`
✅ Either write the content now or don't add the section.

### 16.9 Infra changes without runbook updates

❌ Adding a new container without updating the deploy doc.
✅ Docs and runbooks travel with infra changes in the same PR.

### 16.10 Silent queue semantics changes

❌ Changing `_job_id` format, retry policy, or status transitions
without a note.
✅ Queue contract changes require explicit design discussion.

### 16.11 Format-driven diffs

❌ Reformatting a file you didn't substantively change.
✅ Format only files you actually edited.

### 16.12 Skipping the plan step

❌ "It's small, no plan needed." Then 8 files later, you're lost.
✅ Always plan, however briefly.

### 16.13 Catching `Exception` to make CI green

❌ Adding `except Exception: pass` to silence a failing test.
✅ Fix the bug or update the test contract intentionally.

### 16.14 `pytest.mark.skip` to bypass failing tests

❌ Skipping a flaky test.
✅ Fix the test, or the underlying flakiness, or delete it
intentionally with a note.

### 16.15 Inventing architecture

❌ "There's no documented pattern for this — I'll just add a
`Coordinator` class."
✅ Surface the gap. Ask. Wait for an ADR or explicit guidance.

### 16.16 "Equivalent" library swap

❌ Replacing `arq` with `dramatiq`, `python-telegram-bot` with
`aiogram`, `structlog` with `loguru` because the new one is "more
modern".
✅ All four are locked by ADR-0005. Use them. Surface the reason
for swapping; if it survives review, write the superseding ADR.

### 16.17 In-memory cache with correctness implications

❌ "I'll cache `MediaInfo` in a module-level dict so we don't
re-call yt-dlp."
✅ Workers are stateless (§5.1, ADR-0005 §3.4). Cache in Redis
(`media_cache` table or namespaced key) with explicit TTL, or
not at all.

### 16.18 Adding a "small helper" that breaks the layer rules

❌ Putting a `db_session` accessor in `app/utils/` so `app/bot/`
can call it.
✅ Bot calls a use case; the use case uses a repository protocol;
the protocol is implemented in `app/infrastructure/db/`. No
shortcuts.

### 16.19 Treating `composition.py` as a junk drawer

❌ Wiring random helpers, registering ad-hoc singletons, mixing
test-only fakes into the production composition.
✅ `composition.py` wires **exactly** the dependencies declared
by use cases. Test composition lives in `app/tests/conftest.py`.

### 16.20 Editing `migrations/versions/*.py` after merge

❌ Tweaking a migration that's already been applied somewhere
(staging, prod, even teammate's local DB).
✅ Migrations are immutable after they ship. Write a new migration
that fixes the previous one.

---

## 16A. Common mistakes by AI agents (recipe → recovery)

These are the high-frequency slips observed in past AI-authored PRs.
Each row is paired with the cheapest fix, the rule it breaks, and the
**principle** (§1.A) that was violated.

| # | Mistake | Symptom in diff | Cheapest fix | Rule | Principle |
|---|---|---|---|---|---|
| 1 | Started coding without reading docs | imports / patterns disagree with `02-architecture.md` | revert; do §2 startup protocol; re-plan | §2 | P2 |
| 2 | Skipped the §3 plan | files outside the original scope appear | revert drive-bys; write the plan now; resume | §3 | P1, P2 |
| 3 | Added DB lookup inside a Telegram handler | `from app.infrastructure...` in `app/bot/handlers/...` | move logic to a use case; handler calls use case | §5.1 | P6, P5 |
| 4 | Returned an ORM model from a repository | use case sees `<X>Model` instead of domain entity | map to domain entity inside the repo impl | §5.1 / §5.2 | P5 |
| 5 | Forgot to register a new arq task | `WorkerSettings.functions` missing the new function | add it; restart worker locally to confirm | §12 | P7 |
| 6 | Used `subprocess.run(..., shell=True)` for ffmpeg | `shell=True` shows up in diff | switch to `asyncio.create_subprocess_exec(arg, arg)` | §14.1 | P11 |
| 7 | Logged a full URL or token | `_logger.info("...", url=raw_url)` with secret-bearing URL | strip secrets; log only host + last-4 of token | §13.3 | P11 |
| 8 | Forgot ENUM ALTER in migration | autogen migration is missing `op.execute("ALTER TYPE ...")` | hand-edit migration; add the explicit ALTER | §9.2 | P10 |
| 9 | Added env var only to `.env.example` | compose service can't see the var at runtime | add to `app/config.py`, compose env block, per-stack `.env.example`, `13-` doc | §10.5 | P4 |
| 10 | Catch `Exception` to silence a failing test | `except Exception: pass` or `except: ...` in diff | remove; either fix the bug or update the test contract intentionally | §16.13 | P7, P11 |
| 11 | Used `print()` for "quick debug" | `print(...)` left in diff | replace with `_logger.debug(...)` or remove | Appendix A | P4 |
| 12 | Reformatted unrelated files | massive whitespace/quote churn outside scope | `git checkout HEAD -- <untouched files>` | §4.2, §16.11 | P3, P1 |
| 13 | Forgot to update the doc | behaviour changed but `docs/<topic>.md` untouched | update doc per §15 in the same PR | §15 | P4 |
| 14 | Created a new top-level package | `app/services/`, `app/helpers/`, etc. | move code to the right existing layer; if no fit → ADR | §5.3 | P2 |
| 15 | Stored a 200-byte payload in `callback_data` | `callback_data` >64 bytes; Telegram rejects | use the codec; persist the payload in DB and reference by id | §5.1 (bot rules) | P6 |
| 16 | Renamed something "while at it" | unrelated rename in the diff | revert the rename; open a separate PR if it matters | §4.3, §16.2 | P1, P3 |
| 17 | Edited a shipped migration | `migrations/versions/<old>.py` shows changes | revert; write a new migration | §16.20 | P10 |
| 18 | Dropped a column in the same step that stopped writing | one-shot `op.drop_column` after schema-using code change | split into two migrations / two releases | §9.3 | P10 |
| 19 | Added a new dependency without asking | `pyproject.toml` / requirements gain a line | revert; surface to user with rationale; await approval | §1.4 | P1, P2 |
| 20 | Bypassed `composition.py` to wire something quickly | direct `from app.infrastructure...` import in `app/bot` or `app/api` | route through `composition.build_<layer>()` | §5.1 | P5, P2 |
| 21 | Streamed a public file with `FileResponse` | `FileResponse(path)` in `app/api/public/...` | switch to `X-Accel-Redirect` against the `internal` Nginx location | §14.1 | P11 |
| 22 | Loosened Nginx `internal;` on storage location | `internal;` removed from `location /storage/` | revert; tokens are the security boundary | §14.1 | P11 |
| 23 | Cached `MediaInfo` in a module-level dict | new `_cache: dict = {}` in a worker | move to Redis with explicit TTL or remove entirely | §16.17 | P7 |
| 24 | Embedded yt-dlp specifics in a use case | `format_spec` / `format_id` strings outside provider | move into the provider class; use case stays platform-agnostic | §11.1 | P8 |
| 25 | Added a non-idempotent install step | `apt install` without `--no-install-recommends`, or `mkdir` without `-p` | use the patterns from `25-` §12.2; re-run on healthy system | §10.4 | P9 |

If your diff hits **two or more** rows, stop and re-plan. The change
is signalling a misunderstanding, not a coding slip.

---

## 16B. Mid-edit smell signals (when to halt and re-plan)

While editing, watch for these signals **in your own behaviour**.
Any one of them means: stop, save nothing more, re-plan. Each
signal is tagged with the **principle** (§1.A) it warns you about.

| Signal | Principle at risk | What it really means | Required next step |
|---|---|---|---|
| You opened a 4th file you didn't list in the plan | P1, P3 | scope drift starting | revert untracked changes; refine plan |
| You wrote `# TODO: handle X later` | P1, P4 | you don't actually understand X | stop; either handle X or surface it as a separate task |
| You typed `except Exception:` to make a test pass | P7, P11 | you're hiding the bug | revert; classify the error properly per `16-error-handling.md` |
| You're about to `pip install <new-lib>` | P1, P2 | unapproved dependency | stop; surface to user with §17.9.A-style script |
| You're searching the codebase for "how does it usually work" instead of reading `docs/` | P2 | you skipped §2 | go read the relevant `docs/` topic now |
| You're rewriting a function that wasn't in the plan because "it's clearer this way" | P1, P3 | speculative refactor | revert; note as follow-up |
| You can't remember why a specific edit you just made is needed | P1 | low-confidence change | revert it; ask or document the assumption |
| You're modifying `app/composition.py` for the second time in this change | P2, P5 | DI surface is shifting under you | reconsider whether you've found the right layer for the new code |
| You just copy-pasted a code block from one file to another | P1, P5 | duplication forming | extract to the rightful module per `02-architecture.md` |
| You're touching > 3 files and the change is still labelled "small" | P1 | mis-classification | re-classify as medium per §7; produce design note |
| You can't articulate the goal in one sentence anymore | P1 | scope expanded | restate goal; trim back the diff to match it |
| You're tempted to skip tests "this once" | P4 | quality regression starting | stop; tests are part of done (§8.3) |
| You're about to write `subprocess.run(..., shell=True)` | P11 | shell-injection risk | use `asyncio.create_subprocess_exec(arg, arg)` |
| You're editing a deploy script and didn't re-run it on a clean system | P9 | idempotency unproven | re-run on a system where it has already been applied |
| You wrote a migration without `downgrade()` or a comment justifying empty | P10 | irreversible schema | add `downgrade()` or document why empty |
| You're about to log a full URL / token / secret | P11 | credential leak | strip secrets; log host + last-4 only |
| You're moving yt-dlp config strings out of the provider into a use case | P8 | provider contract leaking | revert; keep all platform specifics inside the provider |
| You're about to register a new env var only in `.env.example` | P4 | config-drift recipe | also add: config.py, per-stack `.env.example`, compose env block, `13-` doc |

These signals exist because every single one was the start of a
PR that had to be rewritten in past projects.

---

## 17. Cursor decision protocol when uncertain

When Cursor encounters uncertainty:

### 17.1 Stop broad modification

If you're not sure, **stop the spread of changes immediately**. Do
not commit "exploratory" code that touches many files.

### 17.2 Narrow the scope

Reduce to the smallest change that addresses the immediate
question. Defer the rest.

### 17.3 Document assumptions

In the PR description (or in chat with the user), state:

> "I am assuming X. If X is wrong, the implementation needs Y."

This makes wrong assumptions falsifiable.

### 17.4 Choose the safer implementation (decision matrix, not vibes)

Score each candidate implementation on the four axes below.
**The candidate with the lowest total score wins.** Ties go to the
candidate that touches fewer locked-by-ADR-0005 areas.

| Axis | 0 (best) | 1 | 2 | 3 (worst) |
|---|---|---|---|---|
| **Blast radius** | one file, one layer | one layer, ≤3 files | two layers | infrastructure + bot/api/worker |
| **Contract change** | no public surface change | internal-only signature change | new optional field on a public surface | breaking change to a public contract |
| **Pattern match** | identical to an existing example in the codebase | small variation on an existing pattern | new pattern, but described in `docs/` | new pattern, not described anywhere |
| **ADR-0005 distance** | unrelated to any locked row | adjacent to a locked row | weakens enforcement of a locked row | proposes changing a locked row |

If the winning candidate scores ≥ 6 total, **do not implement
silently** — surface to the user with the table filled in and ask
for explicit go-ahead.

### 17.5 Surface follow-up work explicitly

If you defer something, name it in the PR description:

> "Follow-up: extract this helper into `app/utils/X.py` once
> there's a second user."

Do not bury follow-ups inside the diff as silent TODOs.

### 17.6 Never invent architecture

If there is no documented pattern and no obvious local analog,
**do not invent one**. Surface the gap to the user. Inventing
architecture silently is the single most damaging mistake an AI
agent can make in this codebase.

### 17.7 Suggest an ADR

When the decision is non-trivial and likely to be revisited:

> "This decision touches the security boundary (NL-1 ↔ NL-2). I
> recommend an ADR before implementation. Draft attached."

### 17.8 Self-correction protocol (when you realise you broke a rule mid-flight)

If, during the change, you notice you've already violated a rule
from §5 / §11 / §12 / §14 (architecture, providers, queue/worker,
security), **do not "fix it forward"** by piling more changes.
Execute this protocol instead:

1. **Stop editing.** Do not save the file you're currently in if
   the violation is in scope.
2. **Inventory the violation.** In one sentence: which rule, in
   which file(s), at which line(s).
3. **Classify the recovery:**

   | Situation | Recovery |
   |---|---|
   | Violation is contained in 1 file you just edited | Revert that file to HEAD; redo the change without the violation |
   | Violation spans multiple files but no commits made yet | Use the editor's undo / `git restore <files>` to peel back to HEAD; redo the change with a corrected plan |
   | Violation already committed, branch not pushed | `git reset --soft HEAD~1` (or `--mixed` if needed); re-stage the *good* parts only |
   | Violation already pushed | Surface to user immediately with §17.9.E refusal; do not push more |
   | Violation is a locked-decision touch (ADR-0005 §2) | Stop, surface, draft a superseding ADR before any further code |

4. **Re-plan.** Update the §3.7 plan to reflect the corrected
   approach. The non-touched-files list now includes the file you
   accidentally drifted into.
5. **Surface.** In the next turn / PR description, write one line:
   "Detected mid-edit violation of `<rule>` in `<file>`; reverted
   and re-planned." Do not hide the slip.

The goal is not to be perfect — it's to **never deepen a wrong
turn**. Self-correction is cheap; quietly compounding a violation
costs reviewer trust.

### 17.9 Refusal scripts (verbatim — copy, fill `<…>`, send)

When a user request hits a red flag (§3.8) or an authority limit
(§1.4), Cursor must respond with one of the templates below
**before** producing any plan or code. Pick the closest match and
fill in `<…>` placeholders.

Each script is also tagged with the **principle** (§1.A) it
defends — name the principle in the response so the user sees
what is being protected.

#### A. Locked decision touched (ADR-0005) — defends P2 (and usually P11)

> Эта правка затрагивает row N из [ADR-0005](adr/0005-locked-architectural-assumptions.md)
> §2: **<row title>**. По протоколу ADR-0005 §6 я не могу
> реализовать это, пока не оформлен superseding ADR.
>
> Предлагаю:
> 1. Открыть `docs/adr/<NNNN>-<slug>.md` со статусом `Proposed`.
> 2. В нём явно указать: `Supersedes: ADR-0005 row N`.
> 3. Описать cascade per ADR-0005 §3.13 — какие соседние locked-решения
>    придётся пересматривать.
> 4. После согласования и принятия ADR — реализовать изменение.
>
> Подтверди, что готов идти этим путём, либо уточни задачу, чтобы
> локированных решений она не касалась.

#### B. Drive-by / scope-creep request — defends P1 + P3

> В исходном запросе была одна задача: «<original goal>». Сейчас
> добавилось «<extra ask>», что выходит за scope (см. §4.5
> "Drive-by edit ban").
>
> Я не буду включать дополнительную правку в этот PR. Варианты:
> - **Отложить** — открою follow-up задачу с TODO + owner.
> - **Сменить scope** — закрываем текущий PR, открываем новый
>   с обоими изменениями как двумя отдельными коммитами.
>
> Что выбираешь?

#### C. Missing context (required reading not done / can't find it) — defends P2

> Чтобы выполнить «<request>», мне нужен один из следующих
> документов / файлов: `<list>`. Не нашёл/не прочитал их.
>
> Прежде чем писать код, прошу:
> - либо подтвердить, что они отсутствуют по дизайну (тогда я открою
>   исследовательский tаск);
> - либо указать путь к ним.
>
> Без этого контекста любой мой код будет в режиме угадывания, что
> по §17.6 запрещено.

#### D. Ambiguous request with architectural consequences — defends P2 + P1

> «<request>» допускает как минимум <N> прочтений:
>
> - **(A)** <interpretation A> — затрагивает <files/layers>; risk: <…>
> - **(B)** <interpretation B> — затрагивает <…>; risk: <…>
> - **(C)** <…>
>
> Каждое прочтение даёт разный PR. Прежде чем планировать, прошу
> выбрать вариант (или указать, что вариант (D) — другой).

#### E. Authority exceeded (push to main / production / destructive op) — defends P9 + P11

> Запрошенное действие выходит за мои полномочия (§1.4): «<action>».
> Я не выполню его автоматически.
>
> Если действительно нужно, выполни вручную после ревью:
> ```
> <exact command(s) the user would run>
> ```
> Я могу подготовить PR, описание и runbook-шаги — но **запускать
> это от твоего имени не буду**.

#### F. Secret / token requested in code — defends P11

> Запрос требует положить секрет (`<NAME>`) в код / репозиторий.
> Это **жёсткий запрет** (§14.1, ADR-0005 §3.9).
>
> Корректный путь:
> 1. Добавить поле в `Settings` (`app/config.py`) с типом и валидацией.
> 2. Прокинуть env-var через `deploy/{single,nl1,nl2}/.env.example` и compose
>    `environment:` block.
> 3. На сервере положить значение в `deploy/{single,nl1,nl2}/.env`
>    (этот файл не коммитится).
> 4. Документировать в `docs/13-config-and-env.md`.
>
> Готов сделать (1), (2), (4) в этом PR. Шаг (3) — твой.

#### G. Skip-the-tests / skip-the-docs request — defends P4

> Тесты и документация перемещаются вместе с кодом (§8.3, §15) —
> это часть definition-of-done, не опция. Если правка слишком
> срочная, чтобы писать тесты и docs, она слишком срочная, чтобы
> мерджить.
>
> Варианты:
> - **Делаю с тестами и доками** (предпочтительно).
> - **Открываю hot-fix PR без тестов**, но с явным TODO + issue
>   с deadline на тесты в течение 24 часов. Подтверди, что
>   принимаешь этот долг.

These scripts are not suggestions — they are the **exact phrasing**
Cursor uses. Deviating into prose loses the operational signal.

---

## 18. Cursor completion protocol

Before declaring a change "ready":

### 18.1 Compare implementation to architecture rules

Mentally walk through §5 and §11 and §12. For each rule, ask: did
I violate it? If yes, fix it.

### 18.2 Diff hygiene review

Read the diff as a stranger would:

- Are there files you don't remember changing?
- Are there lines that have nothing to do with the goal?
- Are there formatting changes outside the touched code?
- Are there dead code blocks, commented-out lines, debug prints?
- Are there silent TODOs?

If yes, remove them.

### 18.3 Verify companions

Confirm:
- Tests added/updated.
- Docs updated per §15.
- Migrations added if schema changed.
- Env vars present in all required places.
- Composition root updated if a new dependency was added.

### 18.4 Verify no regressions

- Lint + typecheck + tests all green.
- No new public exposure.
- No new unhandled exception path.
- No new logging gap (state transitions covered).
- No new security regression (re-skim §14).

### 18.5 Verify deploy story

- If you touched deploy/infra: re-running the relevant script is
  safe.
- If you added a service: it has restart, healthcheck, logging.
- If you added an env var: it's in compose, not just `.env.example`.

### 18.6 Write the PR description

Include:
- Goal (one sentence).
- Files touched (list).
- Migrations (yes/no, file path).
- Docs updated (list).
- Risk + rollback story.
- Test plan (manual + automated).

If you can't write a clear PR description, the change isn't done.

---

## 19. Final operating-procedure checklist

A single-page hard gate. **Every box must be ticked before
declaring done.**

```
PRE-CHANGE
[ ] Read docs/README + 00 + 02 + 03 + 25 + 26
[ ] Read topic-specific doc(s) for the area
[ ] Read ADR-0005 §2 + relevant ADRs
[ ] Scanned request for §3.8 red-flag phrases; none triggered (or: refusal sent)
[ ] Emitted §3.7 plan template, no TBDs, "files NOT touched" non-empty
[ ] Plan does not touch a locked decision (or: ADR drafted)

DURING CHANGE
[ ] Implementing inside-out (domain → app → infra → entry → composition)
[ ] No drive-by edits
[ ] No formatting churn outside changed files
[ ] No silent renames / deletions
[ ] No new top-level packages or layers
[ ] No new dependencies
[ ] No `os.environ`, no `subprocess.run(shell=True)`, no `time.sleep`, no `print`
[ ] No §16B mid-edit smell signal triggered (or: halted + re-planned per §17.8)

POST-CHANGE
[ ] ruff format + ruff check + mypy + pytest -m "not integration" all green
[ ] If shell: bash -n + shellcheck clean
[ ] Tests added/updated
[ ] Docs updated per §15
[ ] Migration added if schema changed
[ ] Env var present in: config.py + .env.example + per-stack .env.example + compose env block + 13- doc
[ ] composition.py updated if new dependency wired
[ ] No secret in diff
[ ] No new public-port exposure
[ ] No path-traversal-friendly path
[ ] No PII / token / cookie in logs
[ ] PR description: goal, files, migrations, docs, risk, test plan

CONFIDENCE
[ ] If uncertain anywhere: scope narrowed, assumption documented, options surfaced
[ ] If invented architecture: stop, ask, do not commit
[ ] If you discovered a mid-flight rule violation: §17.8 self-correction protocol applied and surfaced in PR description

PRINCIPLES (§1.A) — every box mandatory or carrying a documented exception
[ ] P1  minimal safe scope            — diff = plan, "Files NOT touched" still accurate
[ ] P2  architecture first            — layer match (§5), ADR-0005 untouched or superseded
[ ] P3  no unrelated changes          — no drive-bys, no formatting churn, no surprise renames
[ ] P4  docs/tests/config together    — §15 mapping done, tests added, env-var checklist green
[ ] P5  no infra leakage into domain  — domain stdlib-only, application talks to protocols
[ ] P6  no business logic in handlers — bot/api/worker route to one use case, nothing more
[ ] P7  queue semantics preserved     — `_job_id`, max_tries, idempotency, statelessness
[ ] P8  provider contracts explicit   — `BaseProvider` surface unchanged or versioned
[ ] P9  deploy idempotency reasoned   — re-run safe, destructive ops gated by confirm
[ ] P10 db migration reasoned         — alembic + downgrade + deploy-order note + new file
[ ] P11 security defaults intact      — no NL-1 port, no internal-off, ensure_within/sanitize, no logged secrets
```

If a single box is unchecked, **do not declare done**. Exceptions
to a P require an explicit note in the PR description and, for
P2 or P11, a superseding ADR.

---

## 20. Quick reference — Golden Rules built on P1–P11

The eleven principles (§1.A) are the **moral spine** of every
operation. The golden rules below are how Cursor enforces them
in practice.

### 20.1 The eleven principles, one line each

| Code | Hard rule |
|---|---|
| **P1** | Smallest diff that solves the goal. Drive-by edits banned. |
| **P2** | Architecture decided **before** code. ADR-0005 §2 untouched without superseding ADR. |
| **P3** | One PR = one concern. Reformat / rename / refactor → separate PR. |
| **P4** | Docs + tests + config travel with the code in the same PR. |
| **P5** | `app/domain/` and `app/application/` import no infra. Repos return entities, not ORM. |
| **P6** | Handlers route to a single use case. No DB / Redis / yt-dlp inside. |
| **P7** | `_job_id` deterministic; `max_tries` finite; idempotent side-effects; stateless workers. |
| **P8** | Only `BaseProvider` surface is public; platform code stays inside the provider. |
| **P9** | Every deploy / script change is justified for re-run safety; destructive ops gated. |
| **P10** | Schema change ⇒ Alembic migration + downgrade + deploy-order note. Shipped migrations are immutable. |
| **P11** | Security floors (TLS, HSTS, `internal` Nginx, `ensure_within`, `sanitize_filename`, secrets-via-`Settings`) are not negotiable. |

### 20.2 The twelve operating rules (how the principles get enforced)

1. **Read first. Always (P2).** docs/README + 00 + 02 + 03 + 25 + 26 +
   topic doc + ADR-0005 §2 + adjacent ADRs. Skipping this is the #1
   source of rewritten PRs.
2. **Scan the request for red flags (§3.8) before planning (P1, P2, P3).**
   "просто", "заодно", "временно", "почисти", "перепиши" — each
   one means "stop, clarify, possibly refuse".
3. **Emit the §3.7 plan template verbatim before any file edit (P1, P2).**
   No TBDs. No "etc.". `Principles in play` and
   `Files NOT touched` are mandatory and non-empty.
4. **Smallest viable diff (P1, P3).** No drive-by edits. Touch only
   files listed in the plan (§4.1, §4.5).
5. **Stay inside your layer (P5, P6).** No layer-direction violations
   (§5.1). Bot calls one use case; use case calls protocols;
   protocols implemented in `app/infrastructure/`.
6. **ADR-0005 §2 is non-negotiable (P2).** Touching any of the 12
   locked rows requires a superseding ADR **before** any code (§5).
7. **No secrets, no shell strings, no path traversal, no public
   ports on NL-1, no `FileResponse` for public files, no
   `composition.py` bypass (P11).**
8. **Tests + docs + config travel with code in the same PR (P4).**
   §8.3, §15.
9. **Migrations are forward-only, hand-reviewed, two-step for
   destructive changes (P10).** §9. Never edit a shipped migration
   (§16.20); write a new one.
10. **Queue and provider contracts are sacred (P7, P8).** No silent
    `_job_id` changes, no swallowed exceptions, no platform code
    leaking out of providers.
11. **Deploy / script changes always carry an idempotency
    justification (P9).** Re-run on a healthy system; destructive
    ops gated by `confirm`; `bash -n` + `shellcheck` clean.
12. **Done = §19 checklist 100% green, P1–P11 gate honoured.**
    A single unchecked principle box means "not done", not
    "almost done".

### 20.3 When uncertain

Stop, narrow, document, ask (§17). Never invent architecture
(§17.6). Use a §17.9 refusal script when refusing — and name the
defended principle in the response. Mid-flight violations? Apply
§17.8 self-correction protocol; never "fix it forward".

---

## Appendix A — Hard "DO NOT" list (one-liners)

- DO NOT touch a file outside the plan.
- DO NOT add a new top-level package under `app/`.
- DO NOT import infrastructure from domain/application.
- DO NOT use `subprocess.run(..., shell=True)`.
- DO NOT use `time.sleep` in async code.
- DO NOT use `print()` for output.
- DO NOT catch bare `Exception` to silence failures.
- DO NOT read `os.environ` outside `app/config.py`.
- DO NOT change `_job_id` format.
- DO NOT skip `ensure_within` / `sanitize_filename`.
- DO NOT stream public files via `FileResponse`.
- DO NOT drop a column without a 2-step migration plan.
- DO NOT make Nginx storage `location` non-`internal`.
- DO NOT open public ports on NL-1.
- DO NOT log secrets, tokens, cookies, or full URLs.
- DO NOT add a new dependency without user approval.
- DO NOT add a new top-level doc when 00–33 fits.
- DO NOT delete migrations.
- DO NOT bypass `composition.py` for DI.
- DO NOT refactor "while you're here".
- DO NOT mark tests `skip` to make CI green.
- DO NOT invent architecture silently. Stop and ask.
- DO NOT change any row of [ADR-0005 §2](adr/0005-locked-architectural-assumptions.md) without first drafting a superseding ADR.
- DO NOT swap Redis, Postgres, the bash installer, GitHub Actions, Docker Compose, or the topology model (`single` | `split`) — each is locked by ADR-0005 / ADR-0011.
- DO NOT mount `STORAGE_PATH` into the `bot` service.
- DO NOT add a stateful service to NL-2 or a public port to NL-1.
- DO NOT skip the §3.7 verbatim plan template; "informal plans" are not plans.
- DO NOT proceed when a §3.8 red-flag phrase fires; respond with a §17.9 script first.
- DO NOT "fix forward" a mid-flight rule violation; apply §17.8 self-correction.
- DO NOT cache `MediaInfo` (or any job-relevant state) in process memory; workers are stateless.
- DO NOT return ORM models from a use case; map to domain entities inside the repo impl.
- DO NOT edit a shipped migration; write a new one (§16.20).
- DO NOT bundle two unrelated red-flag asks ("просто заодно") into one PR; refuse and split.
- DO NOT respond to a refusal-worthy request with "ok, lite version"; wait for the user.

---

## Appendix B — `.cursor/rules/*.mdc` inventory

The repo also ships persistent Cursor rules in `.cursor/rules/`.
They are **summaries** of the specs in `docs/`, optimised for
token cost. The full content of each rule must always agree with
its source-of-truth doc; if they disagree, the **doc wins** and
the rule is the bug.

| File | Scope (`globs` / `alwaysApply`) | Mirrors |
|---|---|---|
| `.cursor/rules/00-project-overview.mdc` | always | locked decisions in [README](../README.md) + foundation docs |
| `.cursor/rules/10-python-style.mdc` | `app/**/*.py` | [`27-coding-standards.md`](27-coding-standards.md), [`14-logging-observability.md`](14-logging-observability.md) |
| `.cursor/rules/20-architecture-layers.mdc` | `app/**/*.py` | [`02-architecture.md`](02-architecture.md), [`03-project-structure.md`](03-project-structure.md) |
| `.cursor/rules/30-error-and-logging.mdc` | `app/**/*.py` | [`16-error-handling.md`](16-error-handling.md), [`14-logging-observability.md`](14-logging-observability.md) |
| `.cursor/rules/40-config-and-secrets.mdc` | `app/**`, `deploy/**`, `docker/**` | [`13-config-and-env.md`](13-config-and-env.md), [`17-security.md`](17-security.md) |
| `.cursor/rules/50-bash-scripts.mdc` | `deploy/scripts/**/*.sh` | [`20-deployment.md`](20-deployment.md), [`24-runbooks.md`](24-runbooks.md) |
| `.cursor/rules/60-docker-and-deploy.mdc` | `docker/**`, `deploy/**` | [`19-docker-architecture.md`](19-docker-architecture.md), [`20-deployment.md`](20-deployment.md) |
| `.cursor/rules/70-tests.mdc` | `app/tests/**/*.py` | [`18-testing-strategy.md`](18-testing-strategy.md) |
| `.cursor/rules/80-docs.mdc` | `docs/**/*.md`, `README.md` | this file + [`docs/README.md`](README.md) |

Conventions (always follow when adding/editing rule files):

- **At most one `alwaysApply: true`** rule (currently
  `00-project-overview.mdc`). New universal guidance merges into
  it.
- **Glob scope, not always-on**, for everything else. Saves
  context tokens.
- **<100 lines per rule.** Tables and bullets, not prose.
- **Imperative voice.** "Do X." "Never Y."
- **Backlinks**, not full restatements, of `docs/`.
- **No secrets, hostnames, or environment specifics.** They're
  checked into VCS.

To add a new rule:
1. Confirm the canonical spec lives in `docs/`. If not, write it
   there first.
2. Create `.cursor/rules/<NN>-<slug>.mdc` with the frontmatter
   above.
3. Add a row to the table above.
4. PR review checks doc-rule alignment.

---

## Appendix C — Cross-reference & deduplication map

This document is the **operational projection** of explanations that
live elsewhere. The table below is the contract: it tells you, for
every section in this file, **where the underlying knowledge lives**
(canonical) and **what this document adds on top** (operational
delta). The two are *intentionally* duplicated, but the duplication
is *bounded* — when you change one side, you must check the other.

> **How to use this map**
> 1. *Reading this document?* Each entry tells you which other doc to
>    open if you need the *why*, the *full theory*, or the
>    cross-team-readable explanation.
> 2. *Extending this document?* If your topic has a canonical home in
>    column 2, write the explanation **there** and add only the
>    operational projection here. If no canonical home exists yet,
>    write the explanation in the right `docs/` file *first*, then
>    project here. Do **not** start a new long-form explanation in
>    `26-`.
> 3. *Auditing duplication?* Run the lint in §"Drift detection" below.

### C.1 Section-by-section map

| 26- section | Canonical source (doc + §) | What 26- adds (operational delta) |
|---|---|---|
| §1 — Mission of Cursor | [`25-`](25-agent-guide.md) §1, §2 | Posture rule (conservative); explicit non-authority list |
| §1.A — P1–P11 register | [`25-`](25-agent-guide.md) §1.A (canonical names + meaning); [`33-`](33-glossary.md) "Mandatory principles" (lookup table) | Per-code "hard rule" phrased as a refusal-script / pre-commit gate |
| §2 — Startup protocol | [`25-`](25-agent-guide.md) §6.A "plan template" | The 6 mandatory steps as a runnable checklist |
| §3 — Planning protocol | [`25-`](25-agent-guide.md) §6 "safe change protocol"; [`28-`](28-implementation-playbook.md) §3 "standard workflow" | The plan **template** (`Files NOT touched`, `Principles in play`, etc.) |
| §4 — File modification discipline | (Cursor-only — no canonical elsewhere) | Cursor-specific: when to read before edit, when to halt mid-edit, drive-by-edit ban |
| §5 — Architecture preservation | [`25-`](25-agent-guide.md) §3 + §8 (layer rules); [`02-`](02-architecture.md) (layers); [`03-`](03-project-structure.md) (folders); [`adr/0005`](adr/0005-locked-architectural-assumptions.md) §2 (locked rows) | Layer prohibitions as code-review-enforceable one-liners; "inventing layers is forbidden" |
| §6 — Small changes | [`25-`](25-agent-guide.md) §5 (task classification) | Definition of "small" + the 6-step small-change recipe |
| §7 — Medium / large changes | [`25-`](25-agent-guide.md) §5 + §6 | Slice-into-PRs decision rule; "stop and confirm" gates |
| §8 — Pre-commit checklist | [`25-`](25-agent-guide.md) §19 (Definition of Done); [`28-`](28-implementation-playbook.md) §3 step 7+ | Per-PR commands, companion-artifacts checklist, principle compliance gate |
| §9 — Migrations | [`25-`](25-agent-guide.md) §9; [`12-`](12-db-schema.md); [`28-`](28-implementation-playbook.md) §F-DB | "Edit-migration ban", deploy-order note format, online/offline split rules |
| §10 — Deploy / config | [`25-`](25-agent-guide.md) §12; [`20-`](20-deployment.md); [`21-`](21-cicd.md); [`13-`](13-config-and-env.md) | Compose-preservation rules; idempotency re-run test; `.env`-template companion gate |
| §11 — Providers | [`25-`](25-agent-guide.md) §10; [`07-`](07-provider-architecture.md) (contract); [`30-add-new-provider-guide.md`](30-add-new-provider-guide.md) (full how-to) | Cursor-side: only the 3 surface methods are public; no Telegram presentation in providers; per-error-class mapping |
| §12 — Queue / workers | [`25-`](25-agent-guide.md) §11; [`09-queue-and-workers.md`](09-queue-and-workers.md) | `_job_id` determinism rule; `max_tries` ceiling; stateless-worker proof-points |
| §13 — Logging | [`25-`](25-agent-guide.md) §15; [`14-logging-observability.md`](14-logging-observability.md) | Constant `event=` rule; ID-only PII rule; structured `_logger.exception` pattern |
| §14 — Security | [`25-`](25-agent-guide.md) §16; [`17-security.md`](17-security.md); [`34-data-retention-and-privacy.md`](34-data-retention-and-privacy.md) | Hard-prohibition list; `ensure_within` / `sanitize_filename` requirement |
| §15 — Documentation update rules | [`25-`](25-agent-guide.md) §13; [`docs/README.md`](README.md) | Per-change-class doc-update map; doc-as-companion-artifact gate |
| §16 — Anti-patterns | [`25-`](25-agent-guide.md) §17 | Cursor-specific: tool-use anti-patterns (e.g. partial edits, blind regex replace) |
| §16A — Common AI-agent mistakes | [`25-`](25-agent-guide.md) §18 | Recipe → recovery format (failure mode → corrective action) |
| §16B — Mid-edit smell signals | (Cursor-only — no canonical elsewhere) | The "halt and re-plan" trigger list, tagged with the violated P |
| §17 — Decision protocol when uncertain | [`25-`](25-agent-guide.md) §20 (escalation rules) | Cursor-side refusal scripts (A–G), explicit silence-vs-ask matrix |
| §18 — Completion protocol | [`25-`](25-agent-guide.md) §19 (DoD) | Mandatory PR-message template; what to surface vs hide |
| §19 — Final operating-procedure checklist | [`25-`](25-agent-guide.md) §21 (one-screen reference) | Pre-merge gate, per-P verification line items |
| §20 — Golden Rules built on P1–P11 | [`25-`](25-agent-guide.md) §1.A + §21 | Memorable one-liner per P, designed for spaced-repetition recall |
| Appendix A — "DO NOT" list | composite (touches §5, §13, §14, §16) | Single-place lookup of every prohibition in this document |
| Appendix B — `.cursor/rules/*.mdc` inventory | (Cursor-only) | The actual `.cursor/rules/` files, their globs, and which `docs/` they back-link |

### C.2 Where the projection is the *only* place a topic exists

Three sections of this document are intentionally *not* duplicated
elsewhere — they are pure Cursor-operational concerns. **Do** extend
them in this file freely:

- §4 — File modification discipline (Cursor's read-before-edit rule, partial-apply ban, …)
- §16B — Mid-edit smell signals (when to halt and re-plan)
- Appendix B — `.cursor/rules/*.mdc` inventory

### C.3 Drift detection (lightweight pre-merge check)

The duplication surface is bounded only as long as the names and
codes stay aligned. Run this before merging any change that touches
P1–P11 wording, layer rules, or migration / queue / provider rules:

```bash
# 1. P-code names must match across 25-, 26-, 33-
diff \
  <(rg -No 'P[0-9]+\*\*\s+[A-Z][^|]+' docs/25-agent-guide.md | sort -u) \
  <(rg -No 'P[0-9]+\*\*\s+[A-Z][^|]+' docs/26-cursor-rules.md | sort -u) | head

# 2. Glossary table includes all eleven principles
rg -c '^\| \*\*P[0-9]+\*\*' docs/33-glossary.md     # expect: 11

# 3. No new "## " section in 26- without a row in this map
rg '^## ' docs/26-cursor-rules.md | wc -l           # bump C.1 if this grows
```

### C.4 The contract this map enforces

> **If you find yourself writing more than ~5 lines of explanatory
> prose in this document, stop.** The explanation belongs in the
> canonical doc (column 2 above) — leave a one-line projection here
> that links to it. This is what "no business logic in handlers" (P6)
> looks like applied to documentation: `26-` is the *handler*; the
> canonical docs are the *use cases*.
