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
