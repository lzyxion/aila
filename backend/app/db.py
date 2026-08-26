"""DB engine / session 팩토리.

engine 은 **지연 생성**한다. import 시점에 DB 에 붙지 않으므로 DB 없이도
모듈 import·테스트·OpenAPI 생성이 가능하다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """모든 ORM 모델의 베이스. Alembic 은 `Base.metadata` 를 타깃으로 삼는다."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def create_app_engine(url: str | None = None, **kwargs: Any) -> Engine:
    """새 Engine 을 만든다. 테스트에서 SQLite URL 을 넘겨 쓸 수 있다."""
    settings = get_settings()
    url = url or settings.database_url
    options: dict[str, Any] = {"echo": settings.db_echo, "pool_pre_ping": True, "future": True}
    if not url.startswith("sqlite"):
        options["pool_size"] = settings.db_pool_size
        options["max_overflow"] = settings.db_max_overflow
    options.update(kwargs)
    return create_engine(url, **options)


def get_engine() -> Engine:
    """프로세스 전역 Engine (최초 호출 시 생성)."""
    global _engine
    if _engine is None:
        _engine = create_app_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False
        )
    return _session_factory


def get_db() -> Iterator[Session]:
    """FastAPI 의존성: 요청 하나당 세션 하나."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """테스트 훅: 전역 engine/session 팩토리를 버린다."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
