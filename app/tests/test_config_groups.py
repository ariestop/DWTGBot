"""``Settings`` is assembled from topic mixins in ``app/config_groups.py``.

The split must stay invisible to operators: every field lives in exactly
one group, env var names stay flat, and both process env and ``.env``
still feed fields from any group.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.config_groups import SETTINGS_GROUPS


def test_every_field_belongs_to_exactly_one_group() -> None:
    owners: dict[str, list[str]] = {}
    for group in SETTINGS_GROUPS:
        for name in group.model_fields:
            owners.setdefault(name, []).append(group.__name__)

    assert {n: o for n, o in owners.items() if len(o) > 1} == {}
    assert set(owners) == set(Settings.model_fields)


def test_settings_inherits_every_group() -> None:
    assert all(issubclass(Settings, group) for group in SETTINGS_GROUPS)


@pytest.mark.parametrize(
    ("env_name", "raw", "expected"),
    [
        ("POSTGRES_HOST", "10.0.0.5", "10.0.0.5"),
        ("REDIS_PORT", "6380", 6380),
        ("STORAGE_PATH", "/data/storage", Path("/data/storage")),
        ("PROGRESS_TTL_SEC", "900", 900),
        ("METRICS_ENABLED", "true", True),
        ("RL_USER_BURST", "3/30", "3/30"),
        ("INSTANT_DOWNLOAD_ENABLED", "false", False),
    ],
)
def test_flat_env_names_populate_grouped_fields(
    monkeypatch: pytest.MonkeyPatch, env_name: str, raw: str, expected: object
) -> None:
    monkeypatch.setenv(env_name, raw)

    assert getattr(Settings(), env_name) == expected


def test_dotenv_file_still_feeds_grouped_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.delenv("CB_COOLDOWN_SECONDS", raising=False)
    (tmp_path / ".env").write_text("POSTGRES_DB=from_dotenv\nCB_COOLDOWN_SECONDS=42\n")

    settings = Settings()

    assert (settings.POSTGRES_DB, settings.CB_COOLDOWN_SECONDS) == ("from_dotenv", 42)
