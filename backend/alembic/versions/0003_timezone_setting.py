"""app_settings: timezone reserved key + daily limit description refresh

Data-only revision (no schema change).

1. Seeds the reserved ``timezone`` row with ``Asia/Seoul``. The application falls
   back to ``app.config.Settings.default_timezone`` when the row is missing, so
   this is not required for correctness - it exists so the settings screen shows
   the key with a value the operator can edit, exactly like the other reserved
   keys that 0002 seeded.

2. Rewrites the ``daily_analysis_limit`` description. 0002 seeded it with
   "UTC 자정 기준", which is no longer true - the day boundary is now the local
   midnight of the ``timezone`` setting. ``app.app_settings.service._read`` gives
   the **stored** description precedence over the in-code one, so a DB that ran
   0002 would keep showing the stale sentence forever.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMEZONE_KEY = "timezone"
TIMEZONE_DEFAULT = "Asia/Seoul"
TIMEZONE_DESCRIPTION = (
    "일일 분석 한도의 '하루' 를 세는 기준 타임존 (IANA 이름, 예: Asia/Seoul). "
    "이 타임존의 자정에 카운터가 리셋된다."
)

DAILY_LIMIT_KEY = "daily_analysis_limit"
DAILY_LIMIT_DESCRIPTION_NEW = (
    "전역 일일 분석 횟수 상한 (`timezone` 설정의 로컬 자정 기준). "
    "초과하면 분석 시작이 429 로 막힌다."
)
DAILY_LIMIT_DESCRIPTION_OLD = (
    "전역 일일 분석 횟수 상한 (UTC 자정 기준). 초과하면 분석 시작이 429 로 막힌다."
)


def _app_settings_table() -> sa.Table:
    return sa.table(
        "app_settings",
        sa.column("key", sa.String),
        sa.column("value", sa.JSON),
        sa.column("description", sa.Text),
    )


def _set_description(connection: sa.Connection, key: str, description: str) -> None:
    connection.execute(
        sa.text("UPDATE app_settings SET description = :description WHERE key = :key"),
        {"description": description, "key": key},
    )


def upgrade() -> None:
    connection = op.get_bind()

    existing = {
        row[0]
        for row in connection.execute(sa.text("SELECT key FROM app_settings")).fetchall()
    }
    if TIMEZONE_KEY not in existing:
        op.bulk_insert(
            _app_settings_table(),
            [
                {
                    "key": TIMEZONE_KEY,
                    "value": TIMEZONE_DEFAULT,
                    "description": TIMEZONE_DESCRIPTION,
                }
            ],
        )

    _set_description(connection, DAILY_LIMIT_KEY, DAILY_LIMIT_DESCRIPTION_NEW)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("DELETE FROM app_settings WHERE key = :key"), {"key": TIMEZONE_KEY}
    )
    _set_description(connection, DAILY_LIMIT_KEY, DAILY_LIMIT_DESCRIPTION_OLD)
