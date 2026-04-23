"""Async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def build_engine(settings: Settings) -> AsyncEngine:
    """Build the async SQLAlchemy engine.

    Pool sizing is read from Settings (S5): the previous hardcoded
    ``pool_size=5, max_overflow=10`` was the right default for a single
    bot/api process but starves a worker running with ``WORKER_CONCURRENCY``
    > 5 (each download grabs a session for status updates). Production
    deploys should raise ``DB_POOL_SIZE`` on the worker container.
    """
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT_S,
        pool_recycle=settings.DB_POOL_RECYCLE_S,
        connect_args={
            "command_timeout": settings.DB_STATEMENT_TIMEOUT_S,
            "timeout": settings.DB_CONNECT_TIMEOUT_S,
            "server_settings": {
                "idle_in_transaction_session_timeout": str(settings.DB_IDLE_IN_TX_TIMEOUT_MS),
            },
        },
    )


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Transactional context manager: commit on success, rollback on error."""
    session: AsyncSession = sessionmaker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
