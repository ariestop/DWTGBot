# 27 — Coding standards

> Status: Stable
> Audience: backend engineers, AI agents writing Python
> Read next: [`25-agent-guide.md`](25-agent-guide.md), [`02-architecture.md`](02-architecture.md), [`16-error-handling.md`](16-error-handling.md)

This document is the canonical reference for **how Python is written** in
DWTGBot. The configuration that enforces these rules lives in
`pyproject.toml` (ruff + mypy) and `.pre-commit-config.yaml`; this file
documents the *why* and provides examples.

> 🔒 **Locked:** Python 3.11+, `ruff` + `ruff format` + `mypy`. **No
> `black` directly.** See ADR-0003.

---

## 1. Tooling

| Concern | Tool | Source |
|---|---|---|
| Format | `ruff format` (black-compatible) | `pyproject.toml [tool.ruff.format]` |
| Lint | `ruff check --fix` | `pyproject.toml [tool.ruff.lint]` |
| Typecheck | `mypy app` | `pyproject.toml [tool.mypy]` |
| Tests | `pytest -m "not integration"` | `pyproject.toml [tool.pytest.ini_options]` |
| Pre-commit | combines all of the above + shellcheck | `.pre-commit-config.yaml` |

```bash
# one-shot before pushing
ruff format . && ruff check . && mypy app && pytest -m "not integration"
```

### Ruff configuration

```toml
[tool.ruff]
line-length = 100
target-version = "py311"
extend-exclude = ["migrations/versions"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "ASYNC", "S", "C4", "SIM", "PL", "RUF"]
ignore = ["S101", "PLR0913", "PLR2004"]
```

Notable enabled groups:

| Group | What it catches |
|---|---|
| `E`/`F`/`W` | pycodestyle/pyflakes basics |
| `I` | import order (isort-style) |
| `B` | bugbear: real-world bugs (mutable defaults, etc.) |
| `UP` | pyupgrade: modern syntax (`list[int]` not `List[int]`) |
| `ASYNC` | async-specific gotchas (`time.sleep` in async, etc.) |
| `S` | bandit: security (insecure random, shell=True, …) |
| `C4` | comprehension efficiency |
| `SIM` | code simplifications |
| `PL` | pylint subset |
| `RUF` | ruff-specific extras |

Ignored:
- `S101` — `assert` allowed in tests.
- `PLR0913` — many args is OK in services with explicit DI.
- `PLR2004` — magic numbers (we use config policy instead).

Per-file overrides:
- `app/tests/**` → ignore `S105`/`S106` (hard-coded test passwords),
  `PLR2004`.
- `deploy/**` → ignore everything (it's bash + YAML, not Python).

### Mypy configuration

```toml
[tool.mypy]
python_version = "3.11"
strict_optional = true
warn_unused_ignores = true
warn_redundant_casts = true
disallow_untyped_defs = false
ignore_missing_imports = true
exclude = ["migrations/", "app/tests/"]
```

We're not full-strict (yet). Reasons:
- `disallow_untyped_defs = false` — keeps incremental adoption painless;
  new code SHOULD type all defs anyway.
- `ignore_missing_imports = true` — yt-dlp / arq / telegram have
  inconsistent stubs.
- Tests excluded — fixtures use `monkeypatch` heavily; type noise
  outweighs benefit.

> **Rule:** any new file in `app/domain/` or `app/application/` MUST
> have full type annotations. Infrastructure layers SHOULD.

---

## 2. File structure conventions

Every Python module starts with:

```python
"""One-line module purpose.

Optional second paragraph if the why isn't obvious from the path.
"""

from __future__ import annotations

# stdlib
import asyncio
from pathlib import Path
from typing import Any

# third party
import structlog
from sqlalchemy import select

# project
from app.config import Settings
from app.domain.entities.media_info import MediaInfo
from app.exceptions import AppError
from app.logging_config import get_logger

_logger = get_logger(__name__)
```

Rules:
- `from __future__ import annotations` everywhere. Forward refs work
  without quoting; circular imports die.
- Three import groups (stdlib / third-party / project), separated by a
  blank line. `ruff` (`I` rules) handles ordering.
- Module-private constants are `_LIKE_THIS`.
- Module-level logger: `_logger = get_logger(__name__)`.

---

## 3. Naming

| Thing | Convention | Example |
|---|---|---|
| Module | lowercase with underscores | `temp_link_service.py` |
| Class | `CapWords` | `TempLinkService`, `DownloadJob` |
| Function / method | `snake_case` | `register_use`, `extract_first_url` |
| Constant | `UPPER_CASE` | `_VIDEO_HEIGHTS`, `JOB_TIMEOUT_SECONDS` |
| Private | leading underscore | `_classify`, `_logger` |
| Type alias | `CapWords` | `Processor: TypeAlias = ...` |
| Generic / TypeVar | single capital | `T`, `P_co` |
| Pytest test | `test_<verb>_<context>` | `test_register_use_marks_inactive_when_exhausted` |

Acronyms in identifiers: stay capitalised in CapWords (`URL`, `IO`, `DB`)
but lowercase in `snake_case`. Examples: `MediaInfo`, `extract_first_url`.

---

## 4. Typing

Modern syntax only:

```python
# good
def parse(items: list[str], extra: dict[str, int] | None = None) -> tuple[int, ...]: ...

# bad (pre-3.9 style)
from typing import List, Dict, Optional, Tuple
def parse(items: List[str], extra: Optional[Dict[str, int]] = None) -> Tuple[int, ...]: ...
```

- Use `X | None`, not `Optional[X]`.
- Use `list[...]`, `dict[...]`, `tuple[...]`, not `typing.List/Dict/Tuple`.
- Prefer `Iterable`/`Sequence`/`Mapping` from `collections.abc` for
  parameters; concrete `list`/`dict` for return types when ownership
  transfers.
- Domain entities: `@dataclass(slots=True, frozen=False)` (frozen if
  truly immutable). See `app/domain/entities/`.
- Boundary IO models (Telegram payloads, HTTP request bodies): pydantic
  v2.

---

## 5. Dataclasses & domain entities

```python
@dataclass(slots=True)
class TempLink:
    id: int
    token: str
    job_id: int
    file_path: str
    expires_at: datetime
    max_downloads: int
    downloads_count: int = 0
    is_active: bool = True

    def is_usable(self) -> bool: ...
    def register_use(self) -> None: ...
```

- `slots=True` always (smaller, faster, prevents typo'd attributes).
- Methods enforce invariants (e.g. `register_use` increments and
  deactivates).
- No `__init__` overrides unless the dataclass-generated one is
  insufficient.
- No SQLAlchemy / pydantic / framework imports inside `app/domain/`.

---

## 6. Async

We are async-first. Sync IO is a bug.

| Do | Don't |
|---|---|
| `httpx.AsyncClient`, `aiohttp.ClientSession` | `requests`, `urllib.request`, `urllib3` |
| `await asyncio.sleep(t)` | `time.sleep(t)` |
| `asyncio.create_subprocess_exec("a", "b")` | `subprocess.run(...)`, `os.system(...)`, `shell=True` |
| `await asyncio.to_thread(blocking_call, ...)` | calling blocking code on the loop |
| `asyncio.wait_for(coro, timeout=...)` | manual `select` / sentinels |
| `asyncio.Event` for signal coordination | flag polling |
| `aiofiles` only when streaming huge files | regular `open` + `to_thread` for small files |

Cancellation contract:
- All long-running coroutines must respect `asyncio.CancelledError`.
- Always re-raise `CancelledError` (never `except asyncio.CancelledError: pass`).
- Pin per-attempt timeouts; the worker has `JOB_TIMEOUT_SECONDS`
  enforced by arq.

---

## 7. Error handling

(See [`16-error-handling.md`](16-error-handling.md) for the full
contract.)

- Every project exception derives from `AppError`.
- Each entry point uses the **three-tier pattern**:
  1. specific known `AppError`s → log INFO/WARNING + reply with
     `exc.user_message`.
  2. generic `AppError` → log WARNING + reply with `exc.user_message`.
  3. `Exception` → `_logger.exception(...)` + reply generic message.
- Use `raise NewError(...) from exc` for chains.
- Never `except Exception: pass`. Even best-effort blocks must log at
  WARNING.
- Never catch `BaseException`.

---

## 8. Logging

(See [`14-logging-observability.md`](14-logging-observability.md) for
the full contract.)

- Module-level `_logger = get_logger(__name__)`.
- `_logger.info("snake_case_event", key=value, ...)` — structured.
- `_logger.exception(...)` for unexpected errors.
- **Never** `print(...)`. **Never** f-string event names.
- Bind correlation context at the top of every async unit of work:

  ```python
  with bind_context(request_id=req_id, job_id=job.id, user_id=uid, chat_id=cid):
      ...
  ```

---

## 9. Configuration

(See [`13-config-and-env.md`](13-config-and-env.md).)

- Always: `from app.config import get_settings; settings = get_settings()`.
- **Never** read `os.environ[...]` outside `app/config.py`.
- **Never** mutate `Settings` at runtime.

---

## 10. Strings & f-strings

- f-strings everywhere for human-facing text.
- **Not** as logging event names (kills aggregation).
- For SQL, **always** use SQLAlchemy parameter binding; never
  f-string a query.
- For shell args, use `asyncio.create_subprocess_exec(arg1, arg2)`;
  never build a shell string.

---

## 11. Comments & docstrings

- Module docstring: one short line (purpose), optional second
  paragraph (rationale, not behaviour).
- Class docstring: one line per public class; describe the *contract*
  (invariants, lifecycle), not field-by-field.
- Function docstring: only when behaviour isn't obvious from name +
  types. Otherwise it's noise.
- Inline comments: explain *why*, not *what*. The code shows what.

```python
# bad
i = i + 1   # increment i

# good
# yt-dlp returns m3u8 segments interleaved; we want the merged file only.
files = [p for p in files if p.suffix.lower() != ".m3u8"]
```

- Markers: `TODO(name): why`, `FIXME(name): description`. Always own
  them; never anonymous.

---

## 12. Layout: packages & modules

(See [`03-project-structure.md`](03-project-structure.md).)

- One module = one cohesive concept.
- `__init__.py` re-exports only when ergonomic; avoid star-imports.
- No circular imports — if you hit one, the layering is wrong; fix the
  layering.

---

## 13. Performance & memory

- **Don't optimise prematurely.** First write the obvious code, then
  measure. Most of the system is IO-bound (network, disk).
- `slots=True` on dataclasses (already a rule above).
- Stream large data; don't `read()` whole files into memory unless
  bounded.
- Database: prefer one targeted query over many round-trips. We don't
  use ORM lazy loading patterns; sessions are short-lived.

---

## 14. Security in code

(See [`17-security.md`](17-security.md).)

- All paths through `ensure_within(STORAGE_PATH, ...)` and
  `sanitize_filename`.
- All subprocesses via `create_subprocess_exec(arg, arg)`.
- Never log secrets / tokens / cookies / Authorization headers.
- Validate inputs at the boundary; trust nothing from outside.
- SQL via SQLAlchemy parameter binding only.

---

## 15. Tests

(See [`18-testing-strategy.md`](18-testing-strategy.md).)

- Live in `app/tests/test_<thing>.py`.
- Hermetic by default; `@pytest.mark.integration` for opt-in.
- Use fakes for repository / service ABCs; never reach into
  `app.infrastructure.db.*` from a unit test.

---

## 16. Anti-patterns

1. **Star imports** (`from x import *`) — kills static analysis.
2. **Mutable default args** (`def f(x=[])`).
3. **Bare `except:`** or `except BaseException:`.
4. **`print()`** for debugging — use `_logger.debug(...)`.
5. **`os.system` / `subprocess.run(shell=True)`** anywhere.
6. **`time.sleep` in async code.**
7. **Shadowing builtins** (`list`, `id`, `type`, `input`).
8. **Cyclic imports** "fixed" with `if TYPE_CHECKING` instead of fixing
   layering.
9. **Module-level global mutable state** outside `composition.py`.
10. **Inline ORM session creation** outside `composition.py` /
    repositories.
11. **`# type: ignore` without a comment** explaining why.

---

## 17. Common mistakes

| Mistake | Symptom | Fix |
|---|---|---|
| Used `Optional[X]` | `UP007` ruff warning | Switch to `X | None` |
| Used `List[int]` | `UP006` warning | Switch to `list[int]` |
| Forgot `from __future__ import annotations` | Forward-ref errors at import | Add the import |
| Used f-string as log event name | hard to grep / aggregate | Constant string + structured fields |
| Caught `Exception` instead of specific | hides bugs, generic UX | Use the three-tier pattern |
| `# type: ignore[attr-defined]` without a note | future maintainer confused | Add `# type: ignore[attr-defined]  # reason` |
| Mypy fails on a third-party lib | strict mode + missing stubs | First add stubs (`types-X`); only then ignore |
| Forgot to type a public def | mypy may pass (lax mode) but reviewer flags it | Add the annotations |

---

## 18. Future extension points

- Tighten mypy: enable `disallow_untyped_defs` in `app/domain/` and
  `app/application/` first.
- Add `asyncio` cancellation linting (custom ruff plugin).
- Add `S` (bandit) rules currently disabled, after a clean-up pass.
- Adopt `ruff format --check` as a strict gate (already in CI).
