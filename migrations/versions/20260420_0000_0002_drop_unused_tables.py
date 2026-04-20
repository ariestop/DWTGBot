"""drop unused audit_logs and app_settings tables

Revision ID: 0002_drop_unused_tables
Revises: 0001_initial
Create Date: 2026-04-20 00:00:00

S3 (audit fix): both tables were created in the initial schema but
never wired into the application. The migration deletes them outright;
the downgrade rebuilds a minimal compatible shape so a rollback to
0001_initial is still possible. See ``app/infrastructure/db/models.py``
header for the rationale on not adding an audit table back without an
ADR.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_drop_unused_tables"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_event_type", table_name="audit_logs")
    op.drop_table("audit_logs")


def downgrade() -> None:
    # Re-create the original shape so a rollback of *just* this
    # revision succeeds. We deliberately do not restore data — there
    # is none to restore (the tables were never written to in
    # production), and a partial rollback would surprise operators
    # less than a "downgrade does nothing" no-op.
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column(
            "value",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
