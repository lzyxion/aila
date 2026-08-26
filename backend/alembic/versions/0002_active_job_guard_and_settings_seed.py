"""active analysis job guard + app_settings defaults

Two things this revision does, both of which the application cannot do safely
on its own:

1. ``analysis_jobs`` gets a **partial unique index** on ``fingerprint`` limited to
   ``status IN ('pending', 'running')``. The idempotency check in
   ``app.analysis.service.create_analysis_job`` leaves a race window between
   "look for an active job" and "insert a new one"; two people pressing the
   button at the same time used to produce two LLM calls (= duplicate billing).
   The DB constraint is the only thing that actually closes that window.
   PostgreSQL and SQLite both support partial unique indexes.

2. ``app_settings`` gets its three reserved rows seeded. ``daily_analysis_limit``
   and ``sample_retention_days`` carry the documented defaults;
   ``model_pricing`` is seeded **empty** on purpose - prices change without
   notice, so the operator fills the table in and no cost is invented here.
   Seeding also gives the PostgreSQL limit check a row to ``SELECT ... FOR UPDATE``.

0001 is frozen (Phase 0 contract, 1:1 with app/models.py), so this is additive.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_JOB_INDEX = "uq_analysis_jobs_active_fingerprint"
ACTIVE_JOB_PREDICATE = "status IN ('pending', 'running')"

#: 예약 키와 기본값. model_pricing 은 비워 둔다 (단가는 사용자가 채운다).
SETTING_SEEDS: tuple[tuple[str, object, str], ...] = (
    (
        "daily_analysis_limit",
        50,
        "전역 일일 분석 횟수 상한 (UTC 자정 기준). 초과하면 분석 시작이 429 로 막힌다.",
    ),
    (
        "sample_retention_days",
        30,
        "error_samples 보존 일수. 지난 샘플은 삭제한다 (마스킹 규칙 강화는 소급되지 않는다).",
    ),
    (
        "model_pricing",
        {},
        "모델 단가표 {model: {input_per_1k, output_per_1k, currency}}. 비어 있으면 "
        "추정 비용은 계산하지 않고 None 으로 남는다 — 0 으로 적으면 '쌌다'로 읽힌다.",
    ),
)


def _app_settings_table() -> sa.Table:
    return sa.table(
        "app_settings",
        sa.column("key", sa.String),
        sa.column("value", sa.JSON),
        sa.column("description", sa.Text),
    )


def upgrade() -> None:
    connection = op.get_bind()

    # 기존 데이터에 같은 fingerprint 의 active 작업이 둘 이상이면 인덱스를 만들 수 없다.
    # 가장 먼저 시작된 하나만 남기고 나머지는 failed 로 정리한다 (중복 실행의 흔적이므로
    # 조용히 지우지 않고 이력으로 남긴다).
    connection.execute(
        sa.text(
            """
            UPDATE analysis_jobs
               SET status = 'failed',
                   error_message = COALESCE(
                       error_message,
                       '같은 fingerprint 의 중복 진행 작업이라 revision 0002 에서 정리했습니다.'
                   )
             WHERE status IN ('pending', 'running')
               AND id NOT IN (
                   SELECT MIN(id) FROM analysis_jobs
                    WHERE status IN ('pending', 'running')
                    GROUP BY fingerprint
               )
            """
        )
    )

    op.create_index(
        ACTIVE_JOB_INDEX,
        "analysis_jobs",
        ["fingerprint"],
        unique=True,
        sqlite_where=sa.text(ACTIVE_JOB_PREDICATE),
        postgresql_where=sa.text(ACTIVE_JOB_PREDICATE),
    )

    settings_table = _app_settings_table()
    existing = {
        row[0]
        for row in connection.execute(sa.text("SELECT key FROM app_settings")).fetchall()
    }
    rows = [
        {"key": key, "value": value, "description": description}
        for key, value, description in SETTING_SEEDS
        if key not in existing
    ]
    if rows:
        op.bulk_insert(settings_table, rows)


def downgrade() -> None:
    op.drop_index(ACTIVE_JOB_INDEX, table_name="analysis_jobs")
    op.get_bind().execute(
        sa.text(
            "DELETE FROM app_settings WHERE key IN "
            "('daily_analysis_limit', 'sample_retention_days', 'model_pricing')"
        )
    )
