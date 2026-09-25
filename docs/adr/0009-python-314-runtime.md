# ADR-0009: Raise the language floor to Python 3.14

> Status: Accepted (2026-04-19)
> Audience: contributors, AI agents, reviewers, on-call
> Supersedes: row 1 ("Language & runtime: Python 3.11+") of
> [ADR-0005 §2](0005-locked-architectural-assumptions.md#2-decision)

## 1. Context

ADR-0005 row 1 locked the runtime at **CPython 3.11+** (October 2024).
At the time, the dominant constraint was the wheel matrix of compiled
deps — `pydantic-core`, `asyncpg`, `uvloop`, `aiohttp` — none of which
shipped cp313 wheels in late 2024, let alone cp314.

By April 2026 that constraint has fully inverted:

| Dep | cp314 wheels available since |
|---|---|
| `pydantic-core` (≥ 2.42, bundled in `pydantic` ≥ 2.13) | June 2025 (PR #1714) |
| `asyncpg` (≥ 0.31.0) | November 2025 (PR #1279) |
| `uvloop` (≥ 0.22.0) | October 2025 (PR #638) |
| `aiohttp` (≥ 3.13.x, pulled by `python-telegram-bot[ext]`) | September 2025 |
| `python-telegram-bot` (≥ 21.10, pure-Python wheel) | June 2025 (PR #4825 → final in #5004) |
| `yt-dlp` (≥ 2026.x; older versions imported the removed `imp` stdlib) | 2026.01 line |
| `mypy` (≥ 1.13) — parses 3.14 syntax (PEP 749, t-strings) | November 2025 |

Operationally we have also accumulated reasons to want 3.14:

1. **PEP 749 (deferred annotation evaluation)** removes the stylistic
   tax of `from __future__ import annotations` boilerplate that we
   apply at the top of every module today.
2. **PEP 750 (t-strings)** lets us migrate Telegram message templates
   off bespoke `string.Template` shims with safer escaping.
3. **PEP 779 (free-threaded builds)** is now non-experimental; we are
   not adopting it yet, but staying on 3.11 closes the door entirely.
4. **JIT (PEP 744)** is shipped by default on macOS / Windows for the
   3.14 stable line. Worker hot paths (yt-dlp metadata parsing,
   ffmpeg orchestration) measurably benefit on synthetic tests.

The previous bullet under ADR-0005 §3.1 — "Ubuntu 24.04 LTS ships 3.12
as default; 3.11 is the realistic minimum we both develop and test
against" — has lost its force as `python:3.14.4-slim` is the published
official image and is what our CI and Docker images now consume.

## 2. Decision

We **raise the language floor to CPython 3.14** for all production,
CI, developer and Docker-image surfaces of `DWTGBot`:

- `pyproject.toml`: `requires-python = ">=3.14"`,
  `[tool.mypy] python_version = "3.14"`.
  **Caveat:** `[tool.ruff] target-version` is intentionally pinned at
  `"py313"` (one minor below the runtime floor), see §3 below.
- `mypy.ini` удалён: единственный источник настроек mypy и pytest —
  `pyproject.toml` (`[tool.mypy]`, `[tool.pytest.ini_options]`).
- `.github/workflows/ci.yml`: `python-version: "3.14"` for all jobs.
- `docker/{bot,api,worker,backup}.Dockerfile`:
  `ARG PYTHON_VERSION=3.14.4` → `python:3.14.4-slim` base.
- `Makefile`: `PYTHON ?= python3.14`.

Pinned dependencies are bumped to the minimum versions that publish
prebuilt cp314 wheels (see §1 table). Pure-Python pins (`SQLAlchemy`,
`alembic`, `redis-py`, `arq`, `httpx`, `structlog`, `tenacity`,
`prometheus-client`, `fastapi`) are intentionally left at their
existing versions — bumping them would inflate the blast radius
without addressing the runtime floor we are actually raising.

3.13 and earlier are dropped from the support matrix. Existing
deployments must rebuild images from the bumped Dockerfiles before
pulling.

## 3. Consequences

**Accepted:**

- Operators must redeploy from the new images on the next release. No
  rolling 3.11→3.14 mid-cycle support.
- Developers must install Python 3.14 locally (`pyenv install 3.14.4`,
  `brew install python@3.14`, `uv python install 3.14`).
- Pre-commit hooks and `pip install -r requirements/dev.txt` will
  refuse to install on Python ≤3.13 (`requires-python` enforcement).
- New code may use deferred annotations natively, t-string templates,
  and PEP 695 generics without `__future__` imports.

**Tooling caveat — `ruff target-version = "py313"`:**

We deliberately do **not** raise `[tool.ruff] target-version` to
`"py314"` in this change. Under py314, `ruff format` applies the PEP
758 normalization (`except (A, B):` → `except A, B:`), and the
resulting source is unparseable by any tool still running on a 3.13
host: `mypy`, `pytest`, the pre-commit hook, IDE LSPs. Until our
contributor toolchain is uniformly on 3.14 (which lags the runtime
floor by 1–2 quarters in practice), staying on `target-version =
"py313"` lets ruff format/check work everywhere while the runtime
container, the typed bindings (`mypy python_version = "3.14"`) and
the install gate (`requires-python`) all still demand 3.14.

We will flip `target-version` to `"py314"` in a follow-up change once:
- pre-commit hooks default to a 3.14 interpreter on contributor
  machines, **and**
- `mypy` / `pytest-asyncio` releases parse PEP 758 syntax on a 3.13
  host (i.e. they ship a back-ported parser).

**Rejected:**

- We will **not** keep a 3.11 / 3.14 matrix in CI. The maintenance
  cost of dual-version typing/wheels does not match this project's
  contributor surface.
- We will **not** adopt the free-threaded build (`python:3.14t-slim`).
  GIL-removal interactions with `asyncio` + `arq` + `asyncpg` are
  still flagged "EXPERIMENTAL" upstream as of 2026-Q2.
- We will **not** rewrite existing `from __future__ import
  annotations` headers in this change. They are no-ops under 3.14 and
  removing them touches every file. Future PRs may drop them
  opportunistically when modifying a file for unrelated reasons.

## 4. Alternatives considered

1. **Stay on 3.11.** Increasingly forces from-source builds for
   compiled deps; blocks adoption of t-strings / deferred annotations;
   diverges from the official `python:3.14*-slim` Docker images that
   are now the upstream default.
2. **Move to 3.13 instead.** 3.13 was a viable intermediate target a
   year ago, but the cost of bumping is the same as moving directly
   to 3.14, and 3.13 has fewer of the features we want (no t-strings,
   experimental JIT only).
3. **Multi-version CI matrix (3.11 + 3.14).** Doubles CI time and
   forces typing/wheel pins to satisfy the union; rejected (see §3).
4. **Adopt free-threaded 3.14t.** See §3; not yet operationally safe
   for our async + Postgres stack.

## 5. Compliance

For reviewers and AI agents touching the runtime:

- Any change that **lowers** the floor below 3.14 must open a new ADR
  superseding this one. Do not edit the version pin in
  `pyproject.toml` / Dockerfiles in isolation.
- New compiled-dep additions must verify cp314 wheels exist on PyPI
  before being added to `requirements/base.txt`. From-source builds
  in CI are not acceptable.
- `python_version` in mypy config must match the Dockerfile pin
  exactly. Drift here masks real type errors (we hit this twice in
  prior CI incidents — see commit history).

## 6. References

- [ADR-0005 §3.1](0005-locked-architectural-assumptions.md) — the
  superseded 3.11 lock.
- PEP 749 — Deferred Annotation Evaluation.
- PEP 750 — Template Strings.
- PEP 779 — Criteria for supported status of free-threaded Python.
- `pydantic-core` PR #1714 — cp314 wheels.
- `asyncpg` PR #1279 — cp314 wheels + experimental subinterpreters.
- `uvloop` PR #638 — cp314 build fixes.

## 7. History

| Date | Change |
|---|---|
| 2026-04-19 | Initial ADR. Status: Accepted. |
