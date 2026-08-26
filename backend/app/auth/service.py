"""계정 · 세션 서비스.

>>> 왜 무상태 서명 토큰이 아니라 DB 세션인가 <<<
계약이 요구한 것은 **만료(기본 12h)와 로그아웃 무효화** 두 가지다. HMAC 서명
토큰은 만료는 지키지만 무효화를 지키지 못한다 — 서버가 아무 상태도 갖지 않으므로
"로그아웃했다"는 사실을 표현할 방법이 없고, 쿠키를 지우는 것은 브라우저 쪽 정리일
뿐이라 이미 유출된 토큰은 남은 12 시간 동안 계속 유효하다. 사용자별 epoch 를 두어
전부 무효화하는 변형도 있지만, 그것은 "이 세션만 끊기"를 표현하지 못한다.
그래서 세션 행을 둔다. 비용은 요청당 유니크 인덱스 조회 1 회다.

쿠키에 실리는 값은 임의의 256 bit 토큰이고 DB 에는 **SHA-256 해시만** 저장한다
(연결 secret 처럼 Fernet 으로 암호화하지 않는 이유는, 여기서 필요한 것이 복호화가
아니라 대조뿐이기 때문이다 — 복호화할 수 없는 편이 더 안전하다).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password, verify_password
from app.config import get_settings
from app.enums import UserRole
from app.models import User, UserSession

#: 쿠키 토큰의 바이트 수 (base64url 로 43 자).
TOKEN_BYTES = 32

#: 422. starlette 버전별 상수 차이를 피해 숫자를 직접 쓴다 (다른 서비스와 같은 관례).
HTTP_422 = 422


@dataclass(frozen=True)
class Identity:
    """요청 하나가 들고 다니는 인증 주체. ORM 객체를 그대로 넘기지 않는다."""

    user_id: int
    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN.value


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 는 tz 를 버리고 돌려준다 — naive 는 UTC 로 간주한다."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 계정


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def create_user(db: Session, *, username: str, password: str, role: str) -> User:
    """계정 생성. 이름 중복은 409, 값 문제는 422."""
    name = (username or "").strip()
    if not name:
        raise HTTPException(status_code=HTTP_422, detail="username 이 비어 있습니다.")
    if not password:
        raise HTTPException(status_code=HTTP_422, detail="password 가 비어 있습니다.")
    if role not in {member.value for member in UserRole}:
        raise HTTPException(
            status_code=HTTP_422,
            detail=f"role 은 'admin' 또는 'viewer' 여야 합니다 (요청: {role!r}).",
        )

    user = User(username=name, password_hash=hash_password(password), role=role, active=True)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"계정 이름 '{name}' 은 이미 사용 중입니다.",
        ) from None
    db.refresh(user)
    return user


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.id.asc())).all())


def authenticate(db: Session, username: str, password: str) -> User | None:
    """이름 + 비밀번호 검증. 실패 사유는 호출자에게 구분해 주지 않는다.

    "그런 계정 없음" 과 "비밀번호 틀림" 을 나눠 주면 계정 이름 열거가 가능해진다.
    계정이 없을 때도 더미 해시를 한 번 태워 응답 시간 차이를 줄인다.
    """
    user = get_user_by_username(db, (username or "").strip())
    if user is None or not user.active:
        # 타이밍 평탄화 — 실제 계정이 있을 때와 비슷한 비용을 쓴다.
        verify_password(password or "x", _DUMMY_HASH)
        return None
    if not verify_password(password or "", user.password_hash):
        return None
    return user


#: 계정 부재 시 태우는 더미 해시 (모듈 로드 시 1 회 생성).
_DUMMY_HASH = hash_password("aila-timing-equalizer")


# ------------------------------------------------------------------ 세션


def session_ttl() -> timedelta:
    hours = get_settings().session_ttl_hours
    return timedelta(hours=hours if hours > 0 else 12)


def create_session(db: Session, user: User) -> tuple[str, datetime]:
    """세션 발급 → (쿠키에 실을 원문 토큰, 만료 시각). 원문은 여기서만 존재한다."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    expires_at = _now() + session_ttl()
    db.add(
        UserSession(user_id=user.id, token_hash=hash_token(token), expires_at=expires_at)
    )
    db.commit()
    return token, expires_at


def resolve_session(db: Session, token: str | None) -> Identity | None:
    """쿠키 토큰 → `Identity`. 없거나 만료됐거나 계정이 죽었으면 `None`.

    만료된 행은 여기서 지운다 (별도 청소 작업 없이 자연 소멸시키기 위해서다).
    """
    if not token:
        return None
    row = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if row is None:
        return None

    expires_at = _as_utc(row.expires_at)
    if expires_at is None or expires_at <= _now():
        db.delete(row)
        db.commit()
        return None

    user = db.get(User, row.user_id)
    if user is None or not user.active:
        return None
    return Identity(user_id=user.id, username=user.username, role=user.role)


def revoke_session(db: Session, token: str | None) -> bool:
    """로그아웃. 행을 지우므로 같은 쿠키를 다시 써도 통과하지 않는다."""
    if not token:
        return False
    deleted = db.execute(
        delete(UserSession).where(UserSession.token_hash == hash_token(token))
    ).rowcount
    db.commit()
    return bool(deleted)


def purge_expired_sessions(db: Session) -> int:
    """만료 세션 일괄 삭제 (기동 시 1 회)."""
    deleted = db.execute(delete(UserSession).where(UserSession.expires_at < _now())).rowcount
    db.commit()
    return int(deleted or 0)


# ------------------------------------------------------------------ 시드


def seed_admin(db: Session) -> User | None:
    """`AILA_ADMIN_USERNAME` 계정이 없으면 admin 으로 만든다 (앱 기동 시 1 회).

    이미 있으면 **아무것도 하지 않는다** — 비밀번호를 매번 env 값으로 되돌리면
    운영자가 바꾼 값이 재기동마다 기본값으로 돌아간다.

    계정이 하나도 없는 DB 에서 아무도 로그인할 수 없는 상태를 막는 것이 목적이라,
    실패해도 앱 기동은 막지 않는다 (호출자가 예외를 잡는다).
    """
    settings = get_settings()
    username = (settings.admin_username or "admin").strip()
    existing = get_user_by_username(db, username)
    if existing is not None:
        return None
    return create_user(
        db,
        username=username,
        password=settings.admin_password or "admin",
        role=UserRole.ADMIN.value,
    )


def user_count(db: Session) -> int:
    return int(db.scalar(select(func.count(User.id))) or 0)


__all__ = [
    "Identity",
    "authenticate",
    "create_session",
    "create_user",
    "get_user_by_username",
    "hash_token",
    "list_users",
    "purge_expired_sessions",
    "resolve_session",
    "revoke_session",
    "seed_admin",
    "session_ttl",
    "user_count",
]
