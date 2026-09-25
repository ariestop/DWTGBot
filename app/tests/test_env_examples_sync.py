"""Every ``Settings`` field is documented and present in every env example.

``.cursor/rules/40-config-and-secrets.mdc`` requires a new variable to
land in ``docs/13-config-and-env.md`` and in all four ``.env.example``
files. A key may appear commented out (``# KEY=default``) when the
example relies on the ``Settings`` default. Compose-only variables
(``IMAGE_*``, ``LIMIT_*``, ...) are not ``Settings`` fields and are
not checked here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_EXAMPLES = (
    ".env.example",
    "deploy/single/.env.example",
    "deploy/nl1/.env.example",
    "deploy/nl2/.env.example",
)
_KEY_RE = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)


def _example_keys(relative_path: str) -> set[str]:
    return set(_KEY_RE.findall((_REPO_ROOT / relative_path).read_text(encoding="utf-8")))


@pytest.mark.parametrize("relative_path", _ENV_EXAMPLES)
def test_env_example_lists_every_settings_field(relative_path: str) -> None:
    missing = sorted(set(Settings.model_fields) - _example_keys(relative_path))

    assert missing == [], f"{relative_path} is missing: {missing}"


def test_config_doc_describes_every_settings_field() -> None:
    doc = (_REPO_ROOT / "docs/13-config-and-env.md").read_text(encoding="utf-8")

    missing = sorted(name for name in Settings.model_fields if f"`{name}`" not in doc)

    assert missing == [], f"docs/13-config-and-env.md is missing: {missing}"
