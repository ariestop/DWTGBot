"""Import rules for the inner layers (docs/03-project-structure.md §7).

``app/domain`` and ``app/application`` may import only the standard
library and inner project modules. Concrete adapters (infrastructure,
bot, api, workers, composition) and third-party SDKs are reached
through ports wired in ``app/composition.py``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parents[1]
_OUTER_PACKAGES = (
    "app.infrastructure",
    "app.bot",
    "app.api",
    "app.workers",
    "app.composition",
    "app.main_bot",
    "app.main_worker",
    "app.main_api",
)


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
    return found


def _violations(layer: str, extra_forbidden: tuple[str, ...] = ()) -> list[str]:
    forbidden = _OUTER_PACKAGES + extra_forbidden
    problems: list[str] = []
    for path in sorted((_APP / layer).rglob("*.py")):
        for lineno, module in _imports(path):
            top = module.split(".")[0]
            is_third_party = top != "app" and top not in sys.stdlib_module_names
            is_outer = any(module == p or module.startswith(p + ".") for p in forbidden)
            if is_third_party or is_outer:
                problems.append(f"{path.relative_to(_APP.parent)}:{lineno} imports {module}")
    return problems


@pytest.mark.parametrize(
    ("layer", "extra_forbidden"),
    [("domain", ("app.application",)), ("application", ())],
)
def test_inner_layer_imports_only_stdlib_and_inner_modules(
    layer: str, extra_forbidden: tuple[str, ...]
) -> None:
    assert _violations(layer, extra_forbidden) == []
