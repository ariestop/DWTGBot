# Contributing to DWTGBot

Thanks for considering a contribution!

## Local setup

```bash
git clone <repo-url> dwtgbot && cd dwtgbot
make dev-install
make precommit-install
```

## Quality bar

Every change must pass locally:

```bash
make fmt        # ruff format (black-compatible) + ruff --fix
make lint       # ruff check
make typecheck  # mypy
make test       # pytest
```

The project uses **`ruff format`** as the only formatter (it is a byte-compatible
black drop-in). Do not introduce `black` separately.

CI runs the same on every PR (`.github/workflows/ci.yml`) plus shellcheck
for `deploy/scripts/`.

## Dependencies (L8)

The project ships **two files per dependency tier**:

| File | Purpose | Edit by hand? |
|---|---|---|
| `requirements/base.txt`, `dev.txt`, `prod.txt` | Top-level pins with a rationale comment for every bump | ✅ |
| `requirements/base.lock`, `dev.lock`, `prod.lock` | Fully-resolved transitive pins + SHA-256 hashes, produced by `uv pip compile` | ❌ never — regenerate |

Adding or bumping a dependency is always a two-step change:

```bash
# 1. edit requirements/<tier>.txt (add pin + rationale comment)
# 2. regenerate the matching lockfile (keeps every other pin as is)
make lock
# 3. commit both files in the same commit
```

To pull in the newest allowed versions of all transitive dependencies,
run `make lock-upgrade` in a dedicated `chore:` commit.

CI's `lockfile-check` job reruns `uv pip compile` on a clean runner,
seeded with the committed `*.lock` so existing pins are preferred, and
fails the PR if the result diverges — i.e. only when a `*.txt` edit was
not followed by `make lock`, never because upstream published a release. Dockerfiles install from `prod.lock`
with `--require-hashes`, so merging an out-of-sync pair would also
fail image builds.

## Style

- Follow the layered architecture (see [`docs/02-architecture.md`](docs/02-architecture.md)
  and [`docs/03-project-structure.md`](docs/03-project-structure.md)).
  Domain code never imports infrastructure.
- Public functions get type annotations.
- Logs are structured (`structlog`) with at least one identifier
  (`job_id`, `user_id`, `chat_id`, ...) bound via `bind_contextvars` or passed
  as kwargs.
- Don't add comments that just restate the code. Use comments only to capture
  non-obvious intent or constraints.

## Adding a new media platform

See [`docs/30-add-new-provider-guide.md`](docs/30-add-new-provider-guide.md)
(canonical, step-by-step) and the contract in
[`docs/07-provider-architecture.md`](docs/07-provider-architecture.md).

## Pull requests

- Keep PRs focused. One concern per PR.
- Add or update tests for any logic change.
- Update the relevant doc (`README.md`, `docs/*.md`) if behaviour or config
  changes.
- Make sure the PR description explains **why**, not just what.

## Reporting security issues

Please **do not** open a public issue for security problems. Email the
maintainers privately first; we'll coordinate a fix and disclosure timeline.
