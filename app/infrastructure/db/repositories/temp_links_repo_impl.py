"""SQLAlchemy implementation of TempLinksRepository."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities.temp_link import TempLink
from app.domain.repositories.temp_links_repo import TempLinksRepository
from app.infrastructure.db.models import TempLinkModel


class SqlAlchemyTempLinksRepository(TempLinksRepository):
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def create(self, link: TempLink) -> TempLink:
        async with self._sm.begin() as session:
            row = TempLinkModel(
                token=link.token,
                job_id=link.job_id,
                file_path=link.file_path,
                expires_at=link.expires_at,
                max_downloads=link.max_downloads,
                downloads_count=link.downloads_count,
                is_active=link.is_active,
            )
            session.add(row)
            await session.flush()
            # ``created_at`` / ``updated_at`` are server-side defaults
            # (see TimestampMixin). After flush they are expired in the
            # session; a sync attribute read inside ``_to_entity`` would
            # try a refresh that asyncpg refuses outside a greenlet
            # (MissingGreenlet). Refresh explicitly while the session
            # context is still active. Same rationale as the analogous
            # code paths in jobs_repo_impl.
            await session.refresh(row)
            return _to_entity(row)

    async def get_by_token(self, token: str) -> TempLink | None:
        async with self._sm() as session:
            stmt = select(TempLinkModel).where(TempLinkModel.token == token)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return _to_entity(row) if row else None

    async def update(self, link: TempLink) -> TempLink:
        if link.id is None:
            raise ValueError("Cannot update temp link without id")
        async with self._sm.begin() as session:
            row = await session.get(TempLinkModel, link.id)
            if row is None:
                raise LookupError(f"TempLink {link.id} not found")
            row.downloads_count = link.downloads_count
            row.is_active = link.is_active
            row.expires_at = link.expires_at
            row.max_downloads = link.max_downloads
            await session.flush()
            # See ``create()`` — TimestampMixin.updated_at uses onupdate=now()
            # which is server-evaluated; explicit refresh pulls the fresh
            # value before the ORM row leaves the session scope.
            await session.refresh(row)
            return _to_entity(row)

    async def deactivate_expired(self) -> int:
        now = datetime.now(UTC)
        async with self._sm.begin() as session:
            stmt = (
                update(TempLinkModel)
                .where(TempLinkModel.is_active.is_(True), TempLinkModel.expires_at <= now)
                .values(is_active=False)
            )
            result = await session.execute(stmt)
            # SA 2.0.41+ stubs narrowed AsyncSession.execute to Result[Any];
            # at runtime an UPDATE returns CursorResult which carries
            # rowcount. Same idiom used in media_cache_repo_impl.
            return result.rowcount or 0  # type: ignore[attr-defined]

    async def list_inactive_with_files(self, limit: int = 500) -> list[TempLink]:
        async with self._sm() as session:
            stmt = (
                select(TempLinkModel)
                .where(TempLinkModel.is_active.is_(False))
                .order_by(TempLinkModel.expires_at.asc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [_to_entity(r) for r in result.scalars().all()]

    async def try_register_use(self, token: str) -> TempLink | None:
        """Atomic check-and-increment.

        Encodes the same predicates as ``TempLink.is_usable`` directly
        in the WHERE clause, plus the increment in the SET clause.
        Exhaustion does not flip ``is_active`` (see
        ``TempLink.register_use``); the ``downloads_count < max`` filter
        alone refuses further new downloads. Postgres serialises
        concurrent updates of the same row, so two requests on a max=1
        link can no longer both see ``downloads_count < max`` and both
        succeed.

        ``RETURNING`` returns ``None`` when the WHERE clause matched no
        rows — i.e. token missing, inactive, expired, or already at
        the cap. Callers MUST treat ``None`` as "not usable".
        """
        now = datetime.now(UTC)
        async with self._sm.begin() as session:
            stmt = (
                update(TempLinkModel)
                .where(
                    TempLinkModel.token == token,
                    TempLinkModel.is_active.is_(True),
                    TempLinkModel.expires_at > now,
                    TempLinkModel.downloads_count < TempLinkModel.max_downloads,
                )
                .values(
                    downloads_count=TempLinkModel.downloads_count + 1,
                    updated_at=now,
                )
                .returning(TempLinkModel)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return _to_entity(row) if row else None


def _to_entity(row: TempLinkModel) -> TempLink:
    return TempLink(
        id=row.id,
        token=row.token,
        job_id=row.job_id,
        file_path=row.file_path,
        expires_at=row.expires_at,
        max_downloads=row.max_downloads,
        downloads_count=row.downloads_count,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
