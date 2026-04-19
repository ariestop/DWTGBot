# ADR-0003 — `ruff format` for formatting (no `black` directly)

- **Status:** Accepted
- **Date:** 2025-01-XX
- **Deciders:** project owner
- **Tags:** tooling, dev-experience, python

---

## 1. Context

Python projects historically split formatting (`black`) and linting
(`flake8` + plugins, or `pylint`). DWTGBot already uses **`ruff`** for
linting; `ruff format` (since ruff 0.1) implements a black-compatible
formatter in Rust, ~10–100× faster than black on large repos.

Locked decision #14 in the README originally listed "`ruff` + `black`
+ `mypy`". We re-evaluated whether running `black` separately was
worth it, given:

- `ruff format` aims for byte-level black compatibility (and has
  reached it for the styles we care about).
- We already have ruff in CI / pre-commit / Makefile.
- `black` adds a second tool, second config surface, second cache,
  and additional 1–2s on every pre-commit invocation.

Non-goals: changing line length (we keep 100), changing quote style
(we keep double quotes), changing import order rules.

---

## 2. Decision

We use **`ruff format`** as the single Python formatter. We do **not**
run `black` directly. Configuration lives in
`pyproject.toml [tool.ruff.format]`.

The locked-decisions table in `README.md` is updated accordingly:

> Linting: `ruff` + `mypy` (`ruff format` is used, not `black`
> directly).

CI, pre-commit, Makefile, and editor instructions reference
`ruff format` only.

---

## 3. Consequences

### 3.1 Positive

- Faster pre-commit / CI loops.
- One config (`pyproject.toml`), one tool to upgrade.
- Same on-disk output as black (we reviewed diffs on a sample
  pass), so no churn for contributors used to black.

### 3.2 Negative / accepted trade-offs

- We're tracking a younger project (`ruff format`) than `black`. If
  ruff regresses on a corner case, we'll either pin a version or
  fall back to black for one PR.
- Some IDEs default to "Black" as the formatter; users need to
  configure "Ruff" instead. Documented in
  [`27-coding-standards.md`](../27-coding-standards.md).

### 3.3 Operational impact

- `requirements/dev.txt` keeps `ruff` only (no `black`).
- `pre-commit` config has only the ruff hook.

---

## 4. Alternatives considered

### 4.1 Keep `black` (run both)
- **Rejected.** Two tools doing overlapping work, slower CI, no
  meaningful payoff.

### 4.2 Use `black` only
- **Rejected.** We still need ruff for lint rules (`I`, `B`, `S`,
  `UP`, `ASYNC`, `C4`, `SIM`, `PL`, `RUF`); dropping ruff is a
  bigger loss than dropping black.

### 4.3 No formatter (just lint)
- **Rejected.** Inconsistent formatting wastes review time and
  generates noisy diffs.

### 4.4 `autopep8` / `yapf`
- **Rejected.** Inferior on speed and ecosystem mass; no
  contemporary advantage over ruff.

---

## 5. Compliance

- CI runs `ruff format --check .` (failure ≠ green build).
- Pre-commit runs `ruff format` (auto-applies) + `ruff check --fix`.
- Reviewers should reject PRs that:
  - Add `black` to any `requirements*.txt`.
  - Add a `pre-commit` hook for `black`.
  - Change `[tool.ruff.format]` without rationale in the PR.
- Docs ([`27-coding-standards.md`](../27-coding-standards.md),
  `CONTRIBUTING.md`, `README.md`) must reflect this decision.

---

## 6. References

- Config: `pyproject.toml [tool.ruff]`, `pyproject.toml [tool.ruff.format]`.
- CI: `.github/workflows/ci.yml`.
- Pre-commit: `.pre-commit-config.yaml`.
- Docs: [`27-coding-standards.md`](../27-coding-standards.md),
  `CONTRIBUTING.md`, `README.md`.
- External: ruff formatter compatibility notes
  <https://docs.astral.sh/ruff/formatter/>.

---

## 7. History

| Date | Status | Note |
|---|---|---|
| 2025-01-XX | Accepted | Replaces "`ruff` + `black` + `mypy`" with "`ruff` + `mypy`" in the locked decisions. README updated in the same change. |
