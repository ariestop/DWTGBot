"""Fresh Postgres schema per test for the integration suite.

Opt-in: set ``DWTGBOT_TEST_POSTGRES_URL`` to a disposable database (see
``test_advisory_lock_postgres.py`` for a docker one-liner; CI sets it in
the ``integration-tests`` job). The schema comes from the SQLAlchemy
models; ``alembic check`` in CI keeps them equal to the migrations.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql import text

from app.infrastructure.db import models as _models  # noqa: F401  registers the tables
from app.infrastructure.db.base import Base

_DSN = os.environ.get("DWTGBOT_TEST_POSTGRES_URL")


async def _drop_all(conn) -> None:  # type: ignore[no-untyped-def]
    await conn.run_sync(Base.metadata.drop_all)
    # Models declare the enums with create_type=False; migrations own them.
    await conn.execute(text("DROP TYPE IF EXISTS job_status CASCADE"))
    await conn.execute(text("DROP TYPE IF EXISTS platform CASCADE"))


@pytest.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    if not _DSN:
        pytest.skip("DWTGBOT_TEST_POSTGRES_URL is not set")
    engine = create_async_engine(_DSN, echo=False, future=True)
    try:
        async with engine.begin() as conn:
            await _drop_all(conn)
            await conn.execute(text("CREATE TYPE platform AS ENUM ('youtube', 'instagram')"))
            await conn.execute(
                text("CREATE TYPE job_status AS ENUM ('pending', 'processing', 'done', 'failed')")
            )
            await conn.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as conn:
            await _drop_all(conn)
        await engine.dispose()
