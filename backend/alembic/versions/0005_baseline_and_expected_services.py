"""policy baseline query + connection expected services

Phase 7. Two additive nullable columns - applying this revision changes no
behaviour until somebody fills the new fields in.

1. ``analysis_policies.baseline_query`` - optional denominator query for the
   policy dashboard. The error selector counts errors; this counts *all* logs
   in the same label scope, giving ingest volume and the error ratio. NULL
   means "not configured": the dashboard then shows no ingest/ratio numbers
   instead of guessing a selector from the error query (a derived selector
   breaks silently the moment the policy query changes shape).

2. ``loki_connections.expected_services`` - optional JSON list of service
   names that are expected to be emitting logs. When set, every query run
   (manual and scheduled - same code path) checks which of these services
   produced at least one line in the run's range and records the missing ones
   as an ``ingest_absent`` warning on the run. NULL or empty list disables the
   check. This is a recorded observation, not an alert - the contract's "no
   automatic triggers beyond auto_analyze_new" still holds.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analysis_policies",
        sa.Column("baseline_query", sa.Text(), nullable=True),
    )
    op.add_column(
        "loki_connections",
        sa.Column("expected_services", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("loki_connections", "expected_services")
    op.drop_column("analysis_policies", "baseline_query")
