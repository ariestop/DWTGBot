"""Real-Postgres behaviour of the repository queries the cleanup worker
and the media cache rely on (audit R7): the orphan reapers, the media
cache upsert / freshness / purge, and temp-link deactivation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.sql import text

from app.domain.entities.download_job import DownloadJob
from app.domain.entities.temp_link import TempLink
from app.domain.enums import JobStatus, Platform
from app.domain.repositories.media_cache_repo import MediaCacheRecord
from app.infrastructure.db.repositories.jobs_repo_impl import SqlAlchemyJobsRepository
from app.infrastructure.db.repositories.media_cache_repo_impl import (
    SqlAlchemyMediaCacheRepository,
)
from app.infrastructure.db.repositories.temp_links_repo_impl import (
    SqlAlchemyTempLinksRepository,
)

pytestmark = pytest.mark.integration


async def _job(repo: SqlAlchemyJobsRepository, *, user_id: int, status: JobStatus) -> int:
    created = await repo.create(
        DownloadJob(
            id=None,
            user_id=user_id,
            chat_id=user_id,
            source_url="https://example.test/x",
            platform=Platform.INSTAGRAM,
            status=status,
        )
    )
    assert created.id is not None
    return created.id


async def _age(sessionmaker, job_id: int, *, seconds: int) -> None:  # type: ignore[no-untyped-def]
    # Raw SQL so the ORM's onupdate=now() does not refresh updated_at.
    async with sessionmaker.begin() as session:
        await session.execute(
            text(
                "UPDATE download_jobs SET updated_at = now() - make_interval(secs => :s) "
                "WHERE id = :id"
            ),
            {"s": seconds, "id": job_id},
        )


# ----------------------------------------------------------------------
# Orphan reapers
# ----------------------------------------------------------------------


async def test_reap_orphan_processing_fails_only_old_processing_rows(sessionmaker) -> None:
    repo = SqlAlchemyJobsRepository(sessionmaker)
    old = await _job(repo, user_id=1, status=JobStatus.PROCESSING)
    fresh = await _job(repo, user_id=1, status=JobStatus.PROCESSING)
    old_pending = await _job(repo, user_id=1, status=JobStatus.PENDING)
    await _age(sessionmaker, old, seconds=7200)
    await _age(sessionmaker, old_pending, seconds=7200)

    assert await repo.reap_orphan_processing(older_than_seconds=3600) == 1

    reaped = await repo.get(old)
    assert reaped is not None and reaped.status is JobStatus.FAILED
    assert reaped.error_message is not None and reaped.error_message.startswith("orphan reaped")
    assert reaped.completed_at is not None
    assert (await repo.get(fresh)).status is JobStatus.PROCESSING  # type: ignore[union-attr]
    assert (await repo.get(old_pending)).status is JobStatus.PENDING  # type: ignore[union-attr]


async def test_reap_orphan_pending_releases_the_user_cap(sessionmaker) -> None:
    repo = SqlAlchemyJobsRepository(sessionmaker)
    stuck = [await _job(repo, user_id=7, status=JobStatus.PENDING) for _ in range(2)]
    for job_id in stuck:
        await _age(sessionmaker, job_id, seconds=20_000)
    fresh_other_user = await _job(repo, user_id=8, status=JobStatus.PENDING)
    blocked = DownloadJob(
        id=None,
        user_id=7,
        chat_id=7,
        source_url="https://example.test/y",
        platform=Platform.INSTAGRAM,
    )
    assert await repo.create_if_under_cap(blocked, cap=2) is None

    assert await repo.reap_orphan_pending(older_than_seconds=14_400) == 2

    for job_id in stuck:
        reaped = await repo.get(job_id)
        assert reaped is not None and reaped.status is JobStatus.FAILED
        assert reaped.error_message == "orphan reaped: job never left the queue"
    assert (await repo.get(fresh_other_user)).status is JobStatus.PENDING  # type: ignore[union-attr]
    assert await repo.create_if_under_cap(blocked, cap=2) is not None


async def test_reapers_reject_a_non_positive_age(sessionmaker) -> None:
    repo = SqlAlchemyJobsRepository(sessionmaker)
    with pytest.raises(ValueError):
        await repo.reap_orphan_pending(older_than_seconds=0)


# ----------------------------------------------------------------------
# Media cache
# ----------------------------------------------------------------------


def _record(url: str, *, title: str, expires_in: timedelta) -> MediaCacheRecord:
    now = datetime.now(UTC)
    return MediaCacheRecord(
        id=None,
        source_url=url,
        platform=Platform.YOUTUBE,
        media_id="m1",
        title=title,
        metadata_json={"kind": "video"},
        expires_at=now + expires_in,
        created_at=now,
        updated_at=now,
    )


async def test_media_cache_upsert_replaces_the_row_for_the_same_url(sessionmaker) -> None:
    repo = SqlAlchemyMediaCacheRepository(sessionmaker)
    url = "https://www.youtube.com/watch?v=abc"

    first = await repo.upsert(_record(url, title="old", expires_in=timedelta(minutes=10)))
    second = await repo.upsert(_record(url, title="new", expires_in=timedelta(minutes=10)))

    assert second.id == first.id
    fresh = await repo.get_fresh(url)
    assert fresh is not None and fresh.title == "new"
    assert fresh.metadata_json == {"kind": "video"}


async def test_media_cache_hides_and_purges_expired_rows(sessionmaker) -> None:
    repo = SqlAlchemyMediaCacheRepository(sessionmaker)
    live = "https://www.youtube.com/watch?v=live"
    stale = "https://www.youtube.com/watch?v=stale"
    await repo.upsert(_record(live, title="live", expires_in=timedelta(minutes=10)))
    await repo.upsert(_record(stale, title="stale", expires_in=timedelta(seconds=-1)))

    assert await repo.get_fresh(stale) is None
    assert await repo.purge_expired() == 1
    assert await repo.get_fresh(live) is not None


# ----------------------------------------------------------------------
# Temp links
# ----------------------------------------------------------------------


async def _link(
    repo: SqlAlchemyTempLinksRepository, job_id: int, *, token: str, expires_in: timedelta
) -> TempLink:
    return await repo.create(
        TempLink(
            id=None,
            token=token,
            job_id=job_id,
            file_path=f"/storage/jobs/{job_id}/{token}.mp4",
            expires_at=datetime.now(UTC) + expires_in,
            max_downloads=3,
        )
    )


async def test_deactivate_expired_touches_only_expired_active_links(sessionmaker) -> None:
    jobs = SqlAlchemyJobsRepository(sessionmaker)
    links = SqlAlchemyTempLinksRepository(sessionmaker)
    job_id = await _job(jobs, user_id=3, status=JobStatus.DONE)
    await _link(links, job_id, token="expired", expires_in=timedelta(seconds=-5))
    await _link(links, job_id, token="live", expires_in=timedelta(hours=1))

    assert await links.deactivate_expired() == 1
    assert await links.deactivate_expired() == 0

    expired = await links.get_by_token("expired")
    live = await links.get_by_token("live")
    assert expired is not None and expired.is_active is False
    assert live is not None and live.is_active is True


async def test_list_inactive_with_files_returns_recent_links_newest_first(sessionmaker) -> None:
    # Rows are never deleted, so without the window the sweep would keep
    # getting the same oldest 500 rows whose files are long gone (R6).
    jobs = SqlAlchemyJobsRepository(sessionmaker)
    links = SqlAlchemyTempLinksRepository(sessionmaker)
    job_id = await _job(jobs, user_id=4, status=JobStatus.DONE)
    await _link(links, job_id, token="ancient", expires_in=timedelta(days=-3))
    await _link(links, job_id, token="older", expires_in=timedelta(hours=-5))
    await _link(links, job_id, token="newer", expires_in=timedelta(minutes=-5))
    await _link(links, job_id, token="live", expires_in=timedelta(hours=1))
    await links.deactivate_expired()

    horizon = datetime.now(UTC) - timedelta(days=1)
    found = await links.list_inactive_with_files(expired_after=horizon, limit=500)
    assert [link.token for link in found] == ["newer", "older"]

    capped = await links.list_inactive_with_files(expired_after=horizon, limit=1)
    assert [link.token for link in capped] == ["newer"]
