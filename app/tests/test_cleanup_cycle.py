"""cleanup_worker._run_cycle: every step runs, a failed file removal is skipped."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain.entities.temp_link import TempLink
from app.workers.cleanup_worker import _run_cycle


def _link(link_id: int, path: Path) -> TempLink:
    return TempLink(
        id=link_id,
        token=f"t{link_id}",
        job_id=link_id,
        file_path=str(path),
        expires_at=datetime.now(UTC),
        max_downloads=1,
        is_active=False,
    )


class _FakeTempLinks:
    def __init__(self, inactive: list[TempLink]) -> None:
        self.inactive = inactive
        self.deactivated_calls = 0

    async def deactivate_expired(self) -> int:
        self.deactivated_calls += 1
        return 2

    async def list_inactive_with_files(self, limit: int = 500) -> list[TempLink]:
        assert limit == 500
        return self.inactive


class _FakeStorage:
    def __init__(self, root: Path, broken: set[Path]) -> None:
        self.root = root
        self.broken = broken
        self.removed: list[Path] = []

    def remove_path(self, path: Path) -> None:
        if path in self.broken:
            raise PermissionError(f"cannot remove {path}")
        path.unlink()
        self.removed.append(path)


class _FakeMediaCache:
    def __init__(self) -> None:
        self.calls = 0

    async def purge_expired(self) -> int:
        self.calls += 1
        return 1


class _FakeJobs:
    def __init__(self) -> None:
        self.reap_ages: list[int] = []

    async def reap_orphan_processing(self, *, older_than_seconds: int) -> int:
        self.reap_ages.append(older_than_seconds)
        return 1

    async def get(self, job_id: int) -> None:
        return None


@dataclass
class _FakeMetrics:
    orphan_dirs: list[int] = field(default_factory=list)

    def inc_storage_orphan_dirs_removed(self, *, count: int) -> None:
        self.orphan_dirs.append(count)


@dataclass
class _FakeComposition:
    temp_links_repo: _FakeTempLinks
    storage: _FakeStorage
    media_cache_repo: _FakeMediaCache
    jobs_repo: _FakeJobs
    job_metrics: _FakeMetrics


@pytest.mark.asyncio
async def test_cycle_skips_a_failed_removal_and_runs_every_step(tmp_path: Path) -> None:
    ok_first = tmp_path / "a.mp4"
    broken = tmp_path / "b.mp4"
    ok_last = tmp_path / "c.mp4"
    gone = tmp_path / "already-deleted.mp4"
    for path in (ok_first, broken, ok_last):
        path.write_bytes(b"x")

    composition = _FakeComposition(
        temp_links_repo=_FakeTempLinks(
            [_link(1, ok_first), _link(2, broken), _link(3, gone), _link(4, ok_last)]
        ),
        storage=_FakeStorage(tmp_path, broken={broken}),
        media_cache_repo=_FakeMediaCache(),
        jobs_repo=_FakeJobs(),
        job_metrics=_FakeMetrics(),
    )

    await _run_cycle(composition, orphan_age_seconds=5400)  # type: ignore[arg-type]

    # The PermissionError on b.mp4 does not stop c.mp4; a file that is
    # already gone is not touched.
    assert composition.storage.removed == [ok_first, ok_last]
    assert broken.exists()
    assert composition.temp_links_repo.deactivated_calls == 1
    assert composition.media_cache_repo.calls == 1
    assert composition.jobs_repo.reap_ages == [5400]
    # No storage/jobs dir → orphan sweep removes nothing, no metric bump.
    assert composition.job_metrics.orphan_dirs == []


@pytest.mark.asyncio
async def test_cycle_propagates_repository_errors(tmp_path: Path) -> None:
    # A DB failure aborts the cycle; _amain logs it and retries next
    # interval (cleanup_cycle_error) instead of crashing the worker.
    class _BrokenTempLinks(_FakeTempLinks):
        async def deactivate_expired(self) -> int:
            raise ConnectionError("postgres down")

    composition = _FakeComposition(
        temp_links_repo=_BrokenTempLinks([]),
        storage=_FakeStorage(tmp_path, broken=set()),
        media_cache_repo=_FakeMediaCache(),
        jobs_repo=_FakeJobs(),
        job_metrics=_FakeMetrics(),
    )

    with pytest.raises(ConnectionError):
        await _run_cycle(composition, orphan_age_seconds=5400)  # type: ignore[arg-type]
    assert composition.media_cache_repo.calls == 0
