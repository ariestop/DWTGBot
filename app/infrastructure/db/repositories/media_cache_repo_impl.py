"""SQLAlchemy implementation of MediaCacheRepository."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.repositories.media_cache_repo import MediaCacheRecord, MediaCacheRepository
from app.infrastructure.db.models import MediaCacheModel


class SqlAlchemyMediaCacheRepository(MediaCacheRepository):
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def get_fresh(self, source_url: str) -> MediaCacheRecord | None:
        now = datetime.now(timezone.utc)
        async with self._sm() as session:
            stmt = select(MediaCacheModel).where(
                MediaCacheModel.source_url == source_url,
                MediaCacheModel.expires_at > now,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _to_record(row) if row else None

    async def upsert(self, record: MediaCacheRecord) -> MediaCacheRecord:
        async with self._sm.begin() as session:
            stmt = (
                insert(MediaCacheModel)
                .values(
                    source_url=record.source_url,
                    platform=record.platform,
                    media_id=record.media_id,
                    title=record.title,
                    metadata_json=record.metadata_json,
                    expires_at=record.expires_at,
                )
                .on_conflict_do_update(
                    index_elements=[MediaCacheModel.source_url],
                    set_={
                        "platform": record.platform,
                        "media_id": record.media_id,
                        "title": record.title,
                        "metadata_json": record.metadata_json,
                        "expires_at": record.expires_at,
                    },
                )
                .returning(MediaCacheModel)
            )
            row = (await session.execute(stmt)).scalar_one()
            return _to_record(row)

    async def purge_expired(self) -> int:
        now = datetime.now(timezone.utc)
        async with self._sm.begin() as session:
            stmt = delete(MediaCacheModel).where(MediaCacheModel.expires_at <= now)
            result = await session.execute(stmt)
            return result.rowcount or 0


def _to_record(row: MediaCacheModel) -> MediaCacheRecord:
    return MediaCacheRecord(
        id=row.id,
        source_url=row.source_url,
        platform=row.platform,
        media_id=row.media_id,
        title=row.title,
        metadata_json=dict(row.metadata_json or {}),
        expires_at=row.expires_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
