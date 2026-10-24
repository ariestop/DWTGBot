"""Docs name files and log events that exist (docs audit 2026-10, step 3).

The audit found runbooks filtering logs on events the code never emits
and docs pointing at files that were renamed or never written. Markdown
links are checked by lychee in CI; this test covers what lychee cannot
see: paths in backticks and event names in log queries.

Placeholders (``<platform>``, ``NNNN``, globs) and dated work logs in
``docs/tasks/`` are skipped. Playbooks
28-30 still describe an early design on purpose (see their header note)
and are left out until docs audit step 4 trims them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

_SKIP_DOCS = {
    "docs/28-implementation-playbook.md",
    "docs/29-feature-development-guide.md",
    "docs/30-add-new-provider-guide.md",
}
# Named in backticks but not files of this repo, on purpose.
_NOT_FILES = {
    "app/core": "forbidden-package example",
    "app/helpers": "forbidden-package example",
    "app/services": "forbidden-package example",
    "app/tools": "planned package (docs/03 §2.8)",
    "deploy/grafana/dashboards/dwtgbot-slo.json": "where to save the sample dashboard",
    "docker/setup-qemu-action": "a GitHub Action, not a path",
    "app/infrastructure/cache/noop_progress_reporter.py": "ADR-0012 records its removal",
}
# A path in backticks, optionally followed by `:line`, `::symbol` or `:Symbol`.
_PATH_RE = re.compile(
    r"`((?:app|deploy|docker|docs|migrations|requirements|\.github|\.claude)/[^`\s:]*)(?::[^`]*)?`"
)
# Placeholders, globs and GitHub Actions (`docker/metadata-action@v5`).
_PLACEHOLDER = re.compile(r"[<>*{}@]|NNNN|000N|XX")
# Event names in log queries: jq select(.event == "x"), LogQL event="x", event=x.
_EVENT_RE = re.compile(r'\.event *== *\\?"([a-z][a-z0-9_]+)|\bevent=\\?"?([a-z][a-z0-9_]+)')


def _docs() -> list[Path]:
    paths = [
        *_ROOT.glob("docs/**/*.md"),
        *_ROOT.glob(".claude/**/*.md"),
        _ROOT / "README.md",
        _ROOT / "CONTRIBUTING.md",
        _ROOT / "CLAUDE.md",
    ]
    # docs/tasks/ are dated work logs: they name files that were removed since.
    return sorted(
        p
        for p in paths
        if p.is_file()
        and "tasks" not in p.relative_to(_ROOT).parts
        and str(p.relative_to(_ROOT)) not in _SKIP_DOCS
    )


def _prose_lines(path: Path) -> list[tuple[int, str]]:
    """Lines with their numbers; fenced blocks count (commands are checked too)."""
    return list(enumerate(path.read_text(encoding="utf-8").splitlines(), start=1))


def _exists(ref: str) -> bool:
    ref = ref.rstrip("/.")
    target = _ROOT / ref
    if target.exists() or target.with_suffix(".py").exists():
        return True
    if target.name == ".env":  # local, gitignored; the example is committed
        return target.with_name(".env.example").exists()
    # Shorthand for a numbered doc: `docs/36-`, `docs/14`, `docs/02-…`.
    stem = target.name.rstrip("-…")
    return ref.startswith("docs/") and stem.isdigit() and any(target.parent.glob(stem + "-*"))


def _code_literals() -> set[str]:
    literals: set[str] = set()
    for path in (_ROOT / "app").rglob("*.py"):
        if "tests" in path.relative_to(_ROOT / "app").parts:
            continue
        literals.update(
            re.findall(r"[\"']([a-z][a-z0-9_]+)[\"']", path.read_text(encoding="utf-8"))
        )
    return literals


def test_paths_in_docs_exist() -> None:
    missing = []
    for doc in _docs():
        for number, line in _prose_lines(doc):
            for ref in _PATH_RE.findall(line):
                if _PLACEHOLDER.search(ref) or ref.rstrip("/") in _NOT_FILES:
                    continue
                if not _exists(ref):
                    missing.append(f"{doc.relative_to(_ROOT)}:{number}: {ref}")
    assert missing == [], "docs name files that do not exist:\n" + "\n".join(missing)


def test_log_events_in_docs_are_emitted_by_the_code() -> None:
    emitted = _code_literals()
    unknown = []
    for doc in _docs():
        for number, line in _prose_lines(doc):
            for match in _EVENT_RE.finditer(line):
                event = match.group(1) or match.group(2)
                if event not in emitted:
                    unknown.append(f"{doc.relative_to(_ROOT)}:{number}: {event}")
    assert unknown == [], "log queries filter on events the code never emits:\n" + "\n".join(
        unknown
    )


@pytest.mark.parametrize("doc", [_ROOT / "CLAUDE.md", *sorted(_ROOT.glob(".claude/**/*.md"))])
def test_agent_files_have_valid_links(doc: Path) -> None:
    # lychee in CI checks docs/ only; CLAUDE.md and .claude/ are checked here.
    text = re.sub(r"```.*?```", "", doc.read_text(encoding="utf-8"), flags=re.S)
    broken = [
        target
        for target in re.findall(r"\]\(([^)\s#]+)", text)
        if not re.match(r"^[a-z]+:", target) and not (doc.parent / target).exists()
    ]
    assert broken == []
