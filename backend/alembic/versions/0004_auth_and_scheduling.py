"""auth accounts/sessions + policy scheduling fields + triggered_by

Phase 5. Three additive changes, no destructive edits to existing revisions.

1. ``users`` / ``user_sessions`` - login accounts and server-side sessions.
   ``users.password_hash`` holds the self-describing stdlib-scrypt format
   produced by ``app.auth.passwords.hash_password`` (``scrypt$n$r$p$salt$hash``);
   no plaintext ever reaches the database. ``user_sessions`` stores only the
   SHA-256 hash of the cookie token, so reading the table does not yield a
   usable cookie, and logout is a row delete (real revocation).

   The admin account itself is NOT seeded here. It is created at application
   startup from ``AILA_ADMIN_USERNAME`` / ``AILA_ADMIN_PASSWORD`` - seeding a
   password from a migration would bake the demo default into every database
   that ever ran this revision, including ones whose operator later changed it.

2. ``analysis_policies`` scheduling fields. Both booleans default to false, so
   applying this revision changes no behaviour until somebody turns a schedule on.

3. ``query_runs.triggered_by`` / ``analysis_jobs.triggered_by`` - "manual" |
   "schedule". Existing rows all predate the scheduler, so the server default
   backfills them as "manual", which is correct rather than merely convenient.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ auth
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "role", sa.String(length=16), nullable=False, server_default="viewer"
        ),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])

    # ------------------------------------------------------------ scheduling
    op.add_column(
        "analysis_policies",
        sa.Column(
            "schedule_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "analysis_policies",
        sa.Column("schedule_interval_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "analysis_policies",
        sa.Column(
            "auto_analyze_new",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # ---------------------------------------------------------- triggered_by
    op.add_column(
        "query_runs",
        sa.Column(
            "triggered_by", sa.String(length=16), nullable=False, server_default="manual"
        ),
    )
    op.add_column(
        "analysis_jobs",
        sa.Column(
            "triggered_by", sa.String(length=16), nullable=False, server_default="manual"
        ),
    )


def downgrade() -> None:
    op.drop_column("analysis_jobs", "triggered_by")
    op.drop_column("query_runs", "triggered_by")
    op.drop_column("analysis_policies", "auto_analyze_new")
    op.drop_column("analysis_policies", "schedule_interval_minutes")
    op.drop_column("analysis_policies", "schedule_enabled")
    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_table("users")
