"""cleanup_worker._run_cycle: every step runs, a failed file removal is skipped."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.domain.entities.temp_link import TempLink
from app.workers.cleanup_worker import _run_cycle, job_dir_sweep_age


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
        self.expired_after: list[datetime] = []

    async def deactivate_expired(self) -> int:
        self.deactivated_calls += 1
        return 2

    async def list_inactive_with_files(
        self, *, expired_after: datetime, limit: int = 500
    ) -> list[TempLink]:
        assert limit == 500
        self.expired_after.append(expired_after)
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
        self.pending_reap_ages: list[int] = []

    async def reap_orphan_processing(self, *, older_than_seconds: int) -> int:
        self.reap_ages.append(older_than_seconds)
        return 1

    async def reap_orphan_pending(self, *, older_than_seconds: int) -> int:
        self.pending_reap_ages.append(older_than_seconds)
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

    await _run_cycle(
        composition, orphan_age_seconds=5400, pending_age_seconds=14400, temp_link_ttl_seconds=3600
    )  # type: ignore[arg-type]

    # The PermissionError on b.mp4 does not stop c.mp4; a file that is
    # already gone is not touched.
    assert composition.storage.removed == [ok_first, ok_last]
    assert broken.exists()
    assert composition.temp_links_repo.deactivated_calls == 1
    assert composition.media_cache_repo.calls == 1
    assert composition.jobs_repo.reap_ages == [5400]
    assert composition.jobs_repo.pending_reap_ages == [14400]
    # Links that expired before the job-dir sweep horizon are left to it.
    (expired_after,) = composition.temp_links_repo.expired_after
    expected = datetime.now(UTC) - timedelta(days=1)
    assert abs((expired_after - expected).total_seconds()) < 60
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
        await _run_cycle(
            composition,
            orphan_age_seconds=5400,
            pending_age_seconds=14400,
            temp_link_ttl_seconds=3600,
        )  # type: ignore[arg-type]
    assert composition.media_cache_repo.calls == 0


@pytest.mark.parametrize(
    ("ttl", "expected"),
    [(3_600, 86_400), (86_400, 90_000), (7 * 86_400, 7 * 86_400 + 3_600)],
)
def test_job_dir_sweep_waits_for_the_longest_temp_link(ttl: int, expected: int) -> None:
    # A DONE job's dir holds the file of its temp link; deleting it before
    # the link expires turns the link into a 410 Gone.
    assert job_dir_sweep_age(ttl) == expected


@pytest.mark.asyncio
async def test_job_dir_sweep_keeps_dirs_younger_than_the_link_ttl(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    two_days_old = jobs_dir / "41"
    two_days_old.mkdir(parents=True)
    (two_days_old / "video.mp4").write_bytes(b"x")
    stamp = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(two_days_old, (stamp, stamp))

    class _DirStorage(_FakeStorage):
        def remove_path(self, path: Path) -> None:
            self.removed.append(path)

    def _composition() -> _FakeComposition:
        return _FakeComposition(
            temp_links_repo=_FakeTempLinks([]),
            storage=_DirStorage(tmp_path, broken=set()),
            media_cache_repo=_FakeMediaCache(),
            jobs_repo=_FakeJobs(),
            job_metrics=_FakeMetrics(),
        )

    week_ttl = _composition()
    await _run_cycle(  # type: ignore[arg-type]
        week_ttl, orphan_age_seconds=5400, pending_age_seconds=14400, temp_link_ttl_seconds=604_800
    )
    assert week_ttl.storage.removed == []

    hour_ttl = _composition()
    await _run_cycle(  # type: ignore[arg-type]
        hour_ttl, orphan_age_seconds=5400, pending_age_seconds=14400, temp_link_ttl_seconds=3600
    )
    assert hour_ttl.storage.removed == [two_days_old]
