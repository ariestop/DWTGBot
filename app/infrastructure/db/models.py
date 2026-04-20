"""SQLAlchemy 2.x ORM models — single place for the DB schema.

NOTE on dropped tables (S3 audit fix)
-------------------------------------
``audit_logs`` and ``app_settings`` were defined here originally but
never wired into any code path. Carrying them was a maintenance
liability: every schema-touching ADR had to remember they existed,
and they cluttered ``alembic check`` output. Migration
``0002_drop_unused_tables`` removes them; the model classes are gone
from this file. If you genuinely need an audit trail, prefer Loki
queries on structured logs (``event_type=...``) — see
``docs/14-logging-observability.md`` — and add a dedicated table
back through a fresh ADR.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import JobStatus, Platform
from app.infrastructure.db.base import Base, TimestampMixin

# Postgres enum types — created/dropped via migrations explicitly so we don't
# depend on SQLAlchemy auto-create behaviour during tests.
platform_enum = PgEnum(
    Platform,
    name="platform",
    create_type=False,
    values_callable=lambda e: [m.value for m in e],
)

job_status_enum = PgEnum(
    JobStatus,
    name="job_status",
    create_type=False,
    values_callable=lambda e: [m.value for m in e],
)


class DownloadJobModel(Base, TimestampMixin):
    __tablename__ = "download_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[Platform] = mapped_column(platform_enum, nullable=False, index=True)
    media_id: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(512))
    selected_option_key: Mapped[str | None] = mapped_column(String(64))
    selected_format: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[JobStatus] = mapped_column(
        job_status_enum, nullable=False, default=JobStatus.PENDING, index=True
    )
    file_path: Mapped[str | None] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    telegram_file_id: Mapped[str | None] = mapped_column(String(255))
    public_url: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    retries_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    temp_links: Mapped[list[TempLinkModel]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("retries_count >= 0", name="retries_non_negative"),
        Index("ix_download_jobs_status_created_at", "status", "created_at"),
    )


class MediaCacheModel(Base, TimestampMixin):
    __tablename__ = "media_cache"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[Platform] = mapped_column(platform_enum, nullable=False)
    media_id: Mapped[str | None] = mapped_column(String(128), index=True)
    title: Mapped[str | None] = mapped_column(String(512))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (UniqueConstraint("source_url", name="uq_media_cache_source_url"),)


class TempLinkModel(Base, TimestampMixin):
    __tablename__ = "temp_links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("download_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    max_downloads: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    downloads_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    job: Mapped[DownloadJobModel] = relationship(back_populates="temp_links")

    __table_args__ = (
        UniqueConstraint("token", name="uq_temp_links_token"),
        CheckConstraint("max_downloads >= 1", name="max_downloads_positive"),
        CheckConstraint("downloads_count >= 0", name="downloads_count_non_negative"),
    )
