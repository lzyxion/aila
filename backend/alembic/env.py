"""Alembic 환경 설정.

DB URL 은 `alembic.ini` 가 아니라 `app.config.Settings`(= `AILA_DATABASE_URL`)에서 온다.
테스트에서는 SQLite URL 을 넣어 `upgrade head` 를 돌릴 수 있다.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# backend/ 를 import 경로에 올린다 (alembic 을 어디서 실행하든 동작하도록).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db import Base  # noqa: E402
from app import models  # noqa: E402,F401  (모델 등록을 위한 import)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 호출자가 이미 URL 을 지정했으면(테스트 등) 그대로 쓴다.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option(
        "sqlalchemy.url", os.getenv("AILA_DATABASE_URL") or get_settings().database_url
    )

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
