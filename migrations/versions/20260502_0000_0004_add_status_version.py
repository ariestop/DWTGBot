"""add status_version optimistic-lock column to download_jobs

Revision ID: 0004_add_status_version
Revises: 0003_index_status_updated_at
Create Date: 2026-05-02 00:00:00

A21: concurrent job updates (worker finish vs cleanup reaper vs retry
bookkeeping) previously used a plain read-modify-write cycle. Two
writers could therefore clobber each other's state silently. Adding a
monotonic ``status_version`` lets the repository do optimistic locking
with ``UPDATE ... WHERE id=? AND status_version=?`` and raise an
explicit conflict when the row changed since it was read.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_add_status_version"
down_revision: str | None = "0003_index_status_updated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "download_jobs",
        sa.Column("status_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_download_jobs_status_version_non_negative",
        "download_jobs",
        "status_version >= 0",
    )
    op.execute("UPDATE download_jobs SET status_version = 0 WHERE status_version IS NULL")
    op.alter_column("download_jobs", "status_version", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "ck_download_jobs_status_version_non_negative",
        "download_jobs",
        type_="check",
    )
    op.drop_column("download_jobs", "status_version")
