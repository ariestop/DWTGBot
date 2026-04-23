"""A20: cleanup worker removes aged orphan job directories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.domain.entities.download_job import DownloadJob
from app.domain.enums import JobStatus, Platform
from app.workers.cleanup_worker import _sweep_orphan_job_dirs


class _FakeStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.removed: list[Path] = []

    def remove_path(self, path: Path) -> None:
        self.removed.append(path)
        if path.exists():
            import shutil

            shutil.rmtree(path, ignore_errors=True)


class _FakeJobsRepo:
    def __init__(self, jobs: dict[int, DownloadJob | None]) -> None:
        self._jobs = jobs

    async def get(self, job_id: int) -> DownloadJob | None:
        return self._jobs.get(job_id)


class _NoopMetrics:
    def inc_storage_orphan_dirs_removed(self, *, count: int) -> None:
        return None


@dataclass
class _FakeComposition:
    storage: _FakeStorage
    jobs_repo: _FakeJobsRepo
    job_metrics: _NoopMetrics


def _job(job_id: int, *, status: JobStatus) -> DownloadJob:
    return DownloadJob(
        id=job_id,
        user_id=1,
        chat_id=1,
        source_url="https://youtube.com/watch?v=abc",
        platform=Platform.YOUTUBE,
        status=status,
    )


def _touch_old(path: Path, *, age_hours: int) -> None:
    ts = (datetime.now(UTC) - timedelta(hours=age_hours)).timestamp()
    path.mkdir(parents=True, exist_ok=True)
    Path(path, "out.bin").write_bytes(b"x")
    import os

    os.utime(path, (ts, ts))


@pytest.mark.asyncio
async def test_sweep_removes_old_dir_for_missing_job(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    old_dir = jobs_root / "100"
    _touch_old(old_dir, age_hours=25)
    composition = _FakeComposition(
        storage=_FakeStorage(tmp_path),
        jobs_repo=_FakeJobsRepo({100: None}),
        job_metrics=_NoopMetrics(),
    )

    removed = await _sweep_orphan_job_dirs(composition, older_than_seconds=24 * 3600)

    assert removed == 1
    assert not old_dir.exists()


@pytest.mark.asyncio
async def test_sweep_keeps_recent_or_processing_dirs(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    recent_dir = jobs_root / "101"
    processing_dir = jobs_root / "102"
    _touch_old(recent_dir, age_hours=1)
    _touch_old(processing_dir, age_hours=25)
    composition = _FakeComposition(
        storage=_FakeStorage(tmp_path),
        jobs_repo=_FakeJobsRepo(
            {
                101: _job(101, status=JobStatus.DONE),
                102: _job(102, status=JobStatus.PROCESSING),
            }
        ),
        job_metrics=_NoopMetrics(),
    )

    removed = await _sweep_orphan_job_dirs(composition, older_than_seconds=24 * 3600)

    assert removed == 0
    assert recent_dir.exists()
    assert processing_dir.exists()


@pytest.mark.asyncio
async def test_sweep_removes_old_terminal_job_dir(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    failed_dir = jobs_root / "103"
    _touch_old(failed_dir, age_hours=25)
    composition = _FakeComposition(
        storage=_FakeStorage(tmp_path),
        jobs_repo=_FakeJobsRepo({103: _job(103, status=JobStatus.FAILED)}),
        job_metrics=_NoopMetrics(),
    )

    removed = await _sweep_orphan_job_dirs(composition, older_than_seconds=24 * 3600)

    assert removed == 1
    assert not failed_dir.exists()
