"""P11 (docs/34-data-retention-and-privacy.md): logs carry no user URLs.

A source or thumbnail URL is user content: it identifies what a user
downloaded and lands in the host journal for 30 days (and in Sentry
when ``SENTRY_DSN`` is set). Log the platform or the host instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[1]
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
_URL_KEYS = {"url", "source_url", "thumbnail", "thumbnail_url", "webpage_url", "link"}


def _is_logger_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _LOG_METHODS:
        return False
    target = func.value
    name = target.id if isinstance(target, ast.Name) else ""
    return "log" in name.lower()


def _url_keywords() -> list[str]:
    found: list[str] = []
    for path in sorted(_APP.rglob("*.py")):
        if "tests" in path.relative_to(_APP).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_logger_call(node):
                for kw in node.keywords:
                    if kw.arg in _URL_KEYS:
                        rel = path.relative_to(_APP.parent)
                        found.append(f"{rel}:{node.lineno} logs {kw.arg}=")
    return found


def test_log_calls_do_not_carry_urls() -> None:
    assert _url_keywords() == []
