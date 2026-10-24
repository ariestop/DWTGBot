"""backup_worker: runs deploy/scripts/backup.sh on an interval, never dies on it."""

from __future__ import annotations

import signal
from pathlib import Path

import pytest

from app.config import get_settings
from app.workers import backup_worker


def _script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "backup.sh"
    script.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    return script


@pytest.mark.asyncio
async def test_run_backup_returns_zero_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "ran"
    monkeypatch.setenv("BACKUP_SCRIPT", str(_script(tmp_path, f"touch {marker}; echo dumped")))

    assert await backup_worker._run_backup() == 0
    assert marker.exists()


@pytest.mark.asyncio
async def test_run_backup_returns_script_exit_code_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pg_dump / rclone failing inside backup.sh: the worker reports the
    # code instead of raising, so the next interval still runs.
    monkeypatch.setenv("BACKUP_SCRIPT", str(_script(tmp_path, "echo 'pg_dump: error' >&2; exit 3")))

    assert await backup_worker._run_backup() == 3


@pytest.mark.asyncio
async def test_run_backup_survives_missing_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BACKUP_SCRIPT", str(tmp_path / "missing.sh"))

    assert await backup_worker._run_backup() == 127


@pytest.mark.asyncio
async def test_loop_keeps_running_after_failure_and_stops_on_sigterm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def fake_backup() -> int:
        calls.append(1)
        if len(calls) == 2:
            signal.raise_signal(signal.SIGTERM)
        return 1  # every run fails

    monkeypatch.setattr(backup_worker, "_run_backup", fake_backup)
    # Below the >=60 floor on purpose: model_copy skips validation, so the
    # loop runs back to back instead of waiting a minute.
    fast = get_settings().model_copy(update={"BACKUP_INTERVAL_SECONDS": 0})
    monkeypatch.setattr(backup_worker, "get_settings", lambda: fast)

    assert await backup_worker._amain() == 0
    assert len(calls) == 2
