"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 00:00:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PLATFORM_VALUES = ("youtube", "instagram")
JOB_STATUS_VALUES = ("pending", "processing", "done", "failed")


def upgrade() -> None:
    platform_enum = postgresql.ENUM(*PLATFORM_VALUES, name="platform", create_type=False)
    job_status_enum = postgresql.ENUM(*JOB_STATUS_VALUES, name="job_status", create_type=False)

    bind = op.get_bind()
    platform_enum.create(bind, checkfirst=True)
    job_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "download_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("platform", platform_enum, nullable=False),
        sa.Column("media_id", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("selected_option_key", sa.String(length=64), nullable=True),
        sa.Column("selected_format", sa.String(length=64), nullable=True),
        sa.Column("status", job_status_enum, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("telegram_file_id", sa.String(length=255), nullable=True),
        sa.Column("public_url", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retries_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extra", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("retries_count >= 0", name="ck_download_jobs_retries_non_negative"),
    )
    op.create_index("ix_download_jobs_user_id", "download_jobs", ["user_id"])
    op.create_index("ix_download_jobs_chat_id", "download_jobs", ["chat_id"])
    op.create_index("ix_download_jobs_platform", "download_jobs", ["platform"])
    op.create_index("ix_download_jobs_status", "download_jobs", ["status"])
    op.create_index(
        "ix_download_jobs_status_created_at",
        "download_jobs",
        ["status", "created_at"],
    )

    op.create_table(
        "media_cache",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("platform", platform_enum, nullable=False),
        sa.Column("media_id", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_url", name="uq_media_cache_source_url"),
    )
    op.create_index("ix_media_cache_media_id", "media_cache", ["media_id"])
    op.create_index("ix_media_cache_expires_at", "media_cache", ["expires_at"])

    op.create_table(
        "temp_links",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column(
            "job_id",
            sa.BigInteger(),
            sa.ForeignKey("download_jobs.id", ondelete="CASCADE", name="fk_temp_links_job_id_download_jobs"),
            nullable=False,
        ),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_downloads", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("downloads_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("token", name="uq_temp_links_token"),
        sa.CheckConstraint("max_downloads >= 1", name="ck_temp_links_max_downloads_positive"),
        sa.CheckConstraint("downloads_count >= 0", name="ck_temp_links_downloads_count_non_negative"),
    )
    op.create_index("ix_temp_links_job_id", "temp_links", ["job_id"])
    op.create_index("ix_temp_links_expires_at", "temp_links", ["expires_at"])
    op.create_index("ix_temp_links_is_active", "temp_links", ["is_active"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_event_type", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_temp_links_is_active", table_name="temp_links")
    op.drop_index("ix_temp_links_expires_at", table_name="temp_links")
    op.drop_index("ix_temp_links_job_id", table_name="temp_links")
    op.drop_table("temp_links")

    op.drop_index("ix_media_cache_expires_at", table_name="media_cache")
    op.drop_index("ix_media_cache_media_id", table_name="media_cache")
    op.drop_table("media_cache")

    op.drop_index("ix_download_jobs_status_created_at", table_name="download_jobs")
    op.drop_index("ix_download_jobs_status", table_name="download_jobs")
    op.drop_index("ix_download_jobs_platform", table_name="download_jobs")
    op.drop_index("ix_download_jobs_chat_id", table_name="download_jobs")
    op.drop_index("ix_download_jobs_user_id", table_name="download_jobs")
    op.drop_table("download_jobs")

    bind = op.get_bind()
    postgresql.ENUM(name="job_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="platform").drop(bind, checkfirst=True)
