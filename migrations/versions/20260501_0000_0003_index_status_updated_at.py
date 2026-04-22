"""add composite index (status, updated_at) on download_jobs

Revision ID: 0003_index_status_updated_at
Revises: 0002_drop_unused_tables
Create Date: 2026-05-01 00:00:00

Audit fix A14: the orphan-reaper sweep in ``cleanup_worker`` runs

    SELECT ... FROM download_jobs
    WHERE status = 'processing' AND updated_at < :cutoff

every ``CLEANUP_INTERVAL_SECONDS``. The initial schema has
``ix_download_jobs_status`` (single-column) and
``ix_download_jobs_status_created_at`` (status+created_at), but no
index keyed on (status, updated_at). Postgres therefore falls back to
a bitmap-OR of the status index plus a Seq Scan filter on updated_at
once the table grows past a few thousand rows — which is exactly the
regime we enter under sustained load.

This migration adds a dedicated composite index so the reaper stays
constant-time. The leading column is ``status`` so the index is also
usable for the ``WHERE status = :x`` queries the worker issues when
selecting the next PENDING job.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_index_status_updated_at"
down_revision: str | None = "0002_drop_unused_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_download_jobs_status_updated_at",
        "download_jobs",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_download_jobs_status_updated_at", table_name="download_jobs")
