"""Only ``app/config.py`` reads the environment (rule 40-config-and-secrets).

Everything else goes through ``Settings``, which validates and documents
each variable; a stray ``os.environ.get`` skipped both (audit R14).
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[1]
_ALLOWED = {_APP / "config.py"}


def _env_reads(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in {"environ", "getenv"}
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            lines.append(node.lineno)
    return lines


def test_only_config_reads_the_environment() -> None:
    offenders = [
        f"{path.relative_to(_APP.parent)}:{line}"
        for path in sorted(_APP.rglob("*.py"))
        if "tests" not in path.relative_to(_APP).parts and path not in _ALLOWED
        for line in _env_reads(path)
    ]
    assert offenders == []
