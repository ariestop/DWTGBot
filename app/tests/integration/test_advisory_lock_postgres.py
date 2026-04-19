"""Integration tests for atomic concurrency primitives (real Postgres).

These tests cover the contracts introduced by ADR-0008:

* ``JobsRepository.create_if_under_cap`` is atomic under concurrent
  enqueues for the same user (advisory lock).
* ``TempLinksRepository.try_register_use`` is atomic under concurrent
  ``/d/<token>`` requests on a ``max_downloads=1`` link.

They are **opt-in**. To run them, point ``DWTGBOT_TEST_POSTGRES_URL``
at a disposable Postgres database, e.g.::

    docker run -d --rm --name dwtgbot-test-pg \\
        -e POSTGRES_PASSWORD=postgres \\
        -e POSTGRES_DB=dwtgbot_test \\
        -p 55432:5432 postgres:16-alpine

    export DWTGBOT_TEST_POSTGRES_URL=\\
        postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/dwtgbot_test
    pytest -m integration app/tests/integration/test_advisory_lock_postgres.py

Without ``DWTGBOT_TEST_POSTGRES_URL`` the whole module is skipped, so
the suite stays green in environments without Postgres (CI default).

The tests rebuild the schema from SQLAlchemy metadata each run; they
do **not** rely on Alembic, so they survive migration drift.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.sql import text

from app.domain.entities.download_job import DownloadJob
from app.domain.entities.temp_link import TempLink
from app.domain.enums import JobStatus, Platform
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import (  # noqa: F401  ensure models are registered on Base
    DownloadJobModel,
    TempLinkModel,
)
from app.infrastructure.db.repositories.jobs_repo_impl import SqlAlchemyJobsRepository
from app.infrastructure.db.repositories.temp_links_repo_impl import (
    SqlAlchemyTempLinksRepository,
)

pytestmark = pytest.mark.integration

_DSN = os.environ.get("DWTGBOT_TEST_POSTGRES_URL")
if not _DSN:
    pytest.skip(
        "DWTGBOT_TEST_POSTGRES_URL is not set — skipping Postgres integration tests",
        allow_module_level=True,
    )


@pytest.fixture
async def sessionmaker():
    engine = create_async_engine(_DSN, echo=False, future=True)
    try:
        async with engine.begin() as conn:
            # Drop tables first; CASCADE removes FKs into temp_links, etc.
            await conn.run_sync(Base.metadata.drop_all)
            # Then drop the Postgres enum types (models declare create_type=False).
            await conn.execute(text("DROP TYPE IF EXISTS job_status CASCADE"))
            await conn.execute(text("DROP TYPE IF EXISTS platform CASCADE"))
            await conn.execute(text("CREATE TYPE platform AS ENUM ('youtube', 'instagram')"))
            await conn.execute(
                text("CREATE TYPE job_status AS ENUM ('pending', 'processing', 'done', 'failed')")
            )
            await conn.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TYPE IF EXISTS job_status CASCADE"))
            await conn.execute(text("DROP TYPE IF EXISTS platform CASCADE"))
        await engine.dispose()


# ----------------------------------------------------------------------
# create_if_under_cap (per-user concurrency cap)
# ----------------------------------------------------------------------


def _make_job(user_id: int, source_url: str = "https://example.test/x") -> DownloadJob:
    return DownloadJob(
        id=None,
        user_id=user_id,
        chat_id=user_id,
        source_url=source_url,
        platform=Platform.YOUTUBE,
        status=JobStatus.PENDING,
    )


async def test_create_if_under_cap_inserts_when_below_cap(sessionmaker) -> None:
    repo = SqlAlchemyJobsRepository(sessionmaker)

    created = await repo.create_if_under_cap(_make_job(user_id=1001), cap=2)

    assert created is not None
    assert created.id is not None
    assert await repo.count_active_for_user(1001) == 1


async def test_create_if_under_cap_rejects_when_at_cap(sessionmaker) -> None:
    repo = SqlAlchemyJobsRepository(sessionmaker)

    first = await repo.create_if_under_cap(_make_job(user_id=2002), cap=1)
    assert first is not None

    rejected = await repo.create_if_under_cap(_make_job(user_id=2002), cap=1)
    assert rejected is None
    assert await repo.count_active_for_user(2002) == 1


async def test_create_if_under_cap_is_atomic_under_concurrency(sessionmaker) -> None:
    """Hammer the cap with parallel coroutines for the same user.

    Without the advisory lock, ``count + insert`` is racy and more than
    ``cap`` rows can land. With the lock, exactly ``cap`` succeed and
    the remainder return ``None``.
    """
    repo = SqlAlchemyJobsRepository(sessionmaker)
    user_id = 3003
    cap = 3
    attempts = 20

    async def attempt() -> bool:
        result = await repo.create_if_under_cap(_make_job(user_id=user_id), cap=cap)
        return result is not None

    outcomes = await asyncio.gather(*(attempt() for _ in range(attempts)))

    successes = sum(1 for ok in outcomes if ok)
    rejects = sum(1 for ok in outcomes if not ok)

    assert successes == cap, f"expected exactly {cap} successes, got {successes}"
    assert rejects == attempts - cap
    assert await repo.count_active_for_user(user_id) == cap


async def test_create_if_under_cap_does_not_serialise_different_users(
    sessionmaker,
) -> None:
    """Different ``user_id`` values must not contend for the same advisory lock."""
    repo = SqlAlchemyJobsRepository(sessionmaker)

    user_ids = [4000 + i for i in range(8)]

    async def enqueue(uid: int) -> bool:
        return (await repo.create_if_under_cap(_make_job(user_id=uid), cap=1)) is not None

    outcomes = await asyncio.gather(*(enqueue(uid) for uid in user_ids))

    assert all(outcomes), "every distinct user should succeed independently"
    for uid in user_ids:
        assert await repo.count_active_for_user(uid) == 1


# ----------------------------------------------------------------------
# try_register_use (single-use temp link)
# ----------------------------------------------------------------------


async def _seed_link(
    sessionmaker,
    *,
    job_id: int,
    max_downloads: int = 1,
    expires_in_seconds: int = 600,
) -> TempLink:
    """Create one job + one active temp link, return the link entity."""
    jobs = SqlAlchemyJobsRepository(sessionmaker)
    links = SqlAlchemyTempLinksRepository(sessionmaker)

    job = await jobs.create(_make_job(user_id=job_id, source_url="https://example.test/y"))
    assert job.id is not None

    link = TempLink(
        id=None,
        token=secrets.token_urlsafe(16),
        job_id=job.id,
        file_path=f"jobs/{job.id}/file.bin",
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
        max_downloads=max_downloads,
    )
    return await links.create(link)


async def test_try_register_use_atomic_single_use(sessionmaker) -> None:
    """Two parallel hits on a max=1 link: exactly one succeeds."""
    seeded = await _seed_link(sessionmaker, job_id=5005, max_downloads=1)
    repo = SqlAlchemyTempLinksRepository(sessionmaker)

    results = await asyncio.gather(
        repo.try_register_use(seeded.token),
        repo.try_register_use(seeded.token),
        repo.try_register_use(seeded.token),
    )

    successes = [r for r in results if r is not None]
    assert len(successes) == 1, f"expected exactly one success, got {len(successes)}"
    winner = successes[0]
    assert winner.downloads_count == 1
    assert winner.is_active is False, "single-use link must auto-deactivate on first use"

    again = await repo.try_register_use(seeded.token)
    assert again is None, "exhausted link must keep returning None"


async def test_try_register_use_respects_multi_use_cap(sessionmaker) -> None:
    """A max=3 link served 5 times in parallel: exactly 3 succeed."""
    seeded = await _seed_link(sessionmaker, job_id=6006, max_downloads=3)
    repo = SqlAlchemyTempLinksRepository(sessionmaker)

    results = await asyncio.gather(*(repo.try_register_use(seeded.token) for _ in range(5)))

    successes = [r for r in results if r is not None]
    assert len(successes) == 3

    final = await repo.get_by_token(seeded.token)
    assert final is not None
    assert final.downloads_count == 3
    assert final.is_active is False


async def test_try_register_use_returns_none_for_expired(sessionmaker) -> None:
    seeded = await _seed_link(sessionmaker, job_id=7007, expires_in_seconds=-10)
    repo = SqlAlchemyTempLinksRepository(sessionmaker)

    assert await repo.try_register_use(seeded.token) is None

    final = await repo.get_by_token(seeded.token)
    assert final is not None
    assert final.downloads_count == 0, "expired link must not have been incremented"


async def test_try_register_use_returns_none_for_unknown_token(sessionmaker) -> None:
    repo = SqlAlchemyTempLinksRepository(sessionmaker)
    assert await repo.try_register_use("does-not-exist") is None
