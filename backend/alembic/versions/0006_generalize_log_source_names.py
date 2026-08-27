"""generalize Loki-specific names in the abstraction layer

Phase 9. Renames only - no shape change, no data change.

- ``loki_connections``            -> ``log_source_connections``
- ``analysis_policies.loki_connection_id`` -> ``log_source_connection_id``
- ``analysis_policies.logql``     -> ``query``

Why now rather than "when the second adapter arrives": the behavioural
abstraction (LogSourceProvider, capability flags, label_mapping, storing the
query in source-native syntax) was paid for in Phase 0, so only names remained
Loki-specific - and names are cheapest to change while the stack is local-only
with zero external API consumers. Waiting would only let more code accrete on
top of the wrong names.

The Loki name stays where it is true: the adapter package ``app/loki`` and
``LokiProvider``. Scenario baselines and adapter docs keep saying "LogQL"
because there they mean the actual query language.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("loki_connections", "log_source_connections")
    op.alter_column(
        "analysis_policies", "loki_connection_id", new_column_name="log_source_connection_id"
    )
    op.alter_column("analysis_policies", "logql", new_column_name="query")


def downgrade() -> None:
    op.alter_column("analysis_policies", "query", new_column_name="logql")
    op.alter_column(
        "analysis_policies", "log_source_connection_id", new_column_name="loki_connection_id"
    )
    op.rename_table("log_source_connections", "loki_connections")
