"""SQLAlchemy implementation of JobsRepository."""

from __future__ import annotations

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.entities.download_job import DownloadJob
from app.domain.enums import JobStatus
from app.domain.repositories.jobs_repo import JobsRepository
from app.infrastructure.db.models import DownloadJobModel


class SqlAlchemyJobsRepository(JobsRepository):
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def create(self, job: DownloadJob) -> DownloadJob:
        async with self._sm.begin() as session:
            row = DownloadJobModel(
                user_id=job.user_id,
                chat_id=job.chat_id,
                source_url=job.source_url,
                platform=job.platform,
                media_id=job.media_id,
                title=job.title,
                selected_option_key=job.selected_option_key,
                selected_format=job.selected_format,
                status=job.status,
                file_path=job.file_path,
                file_size=job.file_size,
                mime_type=job.mime_type,
                telegram_file_id=job.telegram_file_id,
                public_url=job.public_url,
                error_message=job.error_message,
                retries_count=job.retries_count,
                extra=dict(job.extra),
            )
            session.add(row)
            await session.flush()
            return _to_entity(row)

    async def get(self, job_id: int) -> DownloadJob | None:
        async with self._sm() as session:
            row = await session.get(DownloadJobModel, job_id)
            return _to_entity(row) if row else None

    async def update(self, job: DownloadJob) -> DownloadJob:
        if job.id is None:
            raise ValueError("Cannot update job without id")
        async with self._sm.begin() as session:
            row = await session.get(DownloadJobModel, job.id)
            if row is None:
                raise LookupError(f"Job {job.id} not found")
            row.status = job.status
            row.media_id = job.media_id
            row.title = job.title
            row.selected_option_key = job.selected_option_key
            row.selected_format = job.selected_format
            row.file_path = job.file_path
            row.file_size = job.file_size
            row.mime_type = job.mime_type
            row.telegram_file_id = job.telegram_file_id
            row.public_url = job.public_url
            row.error_message = job.error_message
            row.retries_count = job.retries_count
            row.extra = dict(job.extra)
            row.completed_at = job.completed_at
            await session.flush()
            return _to_entity(row)

    async def increment_retries(self, job_id: int) -> int:
        async with self._sm.begin() as session:
            stmt = (
                update(DownloadJobModel)
                .where(DownloadJobModel.id == job_id)
                .values(retries_count=DownloadJobModel.retries_count + 1)
                .returning(DownloadJobModel.retries_count)
            )
            result = await session.execute(stmt)
            value = result.scalar_one()
            return int(value)

    async def count_active_for_user(self, user_id: int) -> int:
        async with self._sm() as session:
            stmt = (
                select(func.count())
                .select_from(DownloadJobModel)
                .where(
                    DownloadJobModel.user_id == user_id,
                    DownloadJobModel.status.in_((JobStatus.PENDING, JobStatus.PROCESSING)),
                )
            )
            result = await session.execute(stmt)
            return int(result.scalar_one())

    async def create_if_under_cap(self, job: DownloadJob, *, cap: int) -> DownloadJob | None:
        """Cap-respecting insert in a single transaction.

        ``pg_advisory_xact_lock`` serialises only checks for the same
        ``user_id``; the lock is released automatically at COMMIT so we
        never need an explicit unlock and a panicking task can't leak
        the lock across requests. Cost: one extra round-trip per
        enqueue, which is invisible next to the actual ``INSERT``.
        """
        if cap < 1:
            raise ValueError("cap must be >= 1")
        async with self._sm.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:uid)"),
                {"uid": int(job.user_id)},
            )
            count_stmt = (
                select(func.count())
                .select_from(DownloadJobModel)
                .where(
                    DownloadJobModel.user_id == job.user_id,
                    DownloadJobModel.status.in_((JobStatus.PENDING, JobStatus.PROCESSING)),
                )
            )
            active = int((await session.execute(count_stmt)).scalar_one())
            if active >= cap:
                return None

            row = DownloadJobModel(
                user_id=job.user_id,
                chat_id=job.chat_id,
                source_url=job.source_url,
                platform=job.platform,
                media_id=job.media_id,
                title=job.title,
                selected_option_key=job.selected_option_key,
                selected_format=job.selected_format,
                status=job.status,
                file_path=job.file_path,
                file_size=job.file_size,
                mime_type=job.mime_type,
                telegram_file_id=job.telegram_file_id,
                public_url=job.public_url,
                error_message=job.error_message,
                retries_count=job.retries_count,
                extra=dict(job.extra),
            )
            session.add(row)
            await session.flush()
            return _to_entity(row)


def _to_entity(row: DownloadJobModel) -> DownloadJob:
    return DownloadJob(
        id=row.id,
        user_id=row.user_id,
        chat_id=row.chat_id,
        source_url=row.source_url,
        platform=row.platform,
        media_id=row.media_id,
        title=row.title,
        selected_option_key=row.selected_option_key,
        selected_format=row.selected_format,
        status=row.status,
        file_path=row.file_path,
        file_size=row.file_size,
        mime_type=row.mime_type,
        telegram_file_id=row.telegram_file_id,
        public_url=row.public_url,
        error_message=row.error_message,
        retries_count=row.retries_count,
        extra=dict(row.extra or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )
