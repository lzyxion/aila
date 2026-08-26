"""`/api/**` 전역 보호 의존성.

한 곳에서 두 가지를 강제한다.

1. **미인증 차단** — `/api/**` 는 전부 세션이 있어야 한다. 예외는 로그인·로그아웃
   두 라우트와 `/api` 밖의 경로(`/health`, `/docs`, `/openapi.json`)뿐이다.
   미인증 응답은 `401 {detail}` 이다 — 프런트가 401 을 가로채 `/login` 으로 보낸다.
2. **viewer 읽기 전용** — `viewer` 는 GET/HEAD/OPTIONS 만, `admin` 은 전부.
   그 외 메서드는 `403 {detail}`.

>>> 왜 미들웨어가 아니라 전역 의존성인가 <<<
의존성은 요청 세션(`get_db`)을 그대로 물려받고 FastAPI 의 예외 처리를 타므로
401/403 응답 형식이 다른 오류와 같아진다. 미들웨어로 하면 DB 세션을 따로 열어야
하고, `HTTPException` 이 아니라 raw `Response` 를 만들게 되어 `{detail}` 형식이
갈라진다.

라우트 화이트리스트를 **경로 문자열**로 판정하는 것은 의도다. 라우터 쪽에
`Depends` 를 붙여 다니는 방식은 새 라우터를 추가하면서 빼먹으면 조용히 열린다 —
기본값이 "닫힘" 인 쪽을 고른다.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import service as auth_service
from app.auth.service import Identity
from app.config import get_settings
from app.db import get_db

#: 인증 없이 통과시키는 `/api` 하위 경로 (접두사를 뺀 값).
PUBLIC_API_PATHS: frozenset[str] = frozenset({"/auth/login", "/auth/logout"})

#: 권한 검사에서 "읽기" 로 보는 메서드.
READ_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

UNAUTHENTICATED_DETAIL = "인증이 필요합니다. 로그인 후 다시 시도하세요."
FORBIDDEN_DETAIL = "viewer 계정은 조회(GET)만 할 수 있습니다."


def session_token(request: Request) -> str | None:
    return request.cookies.get(get_settings().session_cookie_name)


def identity_from_request(request: Request, db: Session) -> Identity | None:
    """요청 → 인증 주체 (없으면 `None`).

    테스트는 이 함수 하나만 대체하면 "항상 admin 세션" 을 만들 수 있다
    (`tests/conftest.py` 의 기본 fixture 가 그렇게 한다).
    """
    return auth_service.resolve_session(db, session_token(request))


def _is_public(path: str, prefix: str) -> bool:
    """`/api` 밖이거나 로그인·로그아웃이면 인증을 요구하지 않는다."""
    if not path.startswith(prefix):
        return True
    relative = path[len(prefix) :] or "/"
    return relative.rstrip("/") in PUBLIC_API_PATHS or relative in PUBLIC_API_PATHS


def enforce_api_auth(request: Request, db: Session = Depends(get_db)) -> Identity | None:
    """전역 의존성. 통과한 요청은 `request.state.identity` 로 주체를 들고 다닌다."""
    prefix = get_settings().api_prefix
    path = request.url.path
    request.state.identity = None

    if not path.startswith(prefix):
        # `/health` 는 **DB 에 붙지 않는** liveness 체크라는 계약이 있다.
        # 세션 조회를 먼저 하면 쿠키가 실린 요청에서 그 계약이 깨진다.
        return None

    identity = identity_from_request(request, db)
    request.state.identity = identity

    if _is_public(path, prefix):
        return identity

    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=UNAUTHENTICATED_DETAIL
        )
    if not identity.is_admin and request.method.upper() not in READ_METHODS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN_DETAIL)
    return identity


def current_identity(request: Request) -> Identity:
    """라우터용 — 이미 전역 의존성이 채워 둔 주체를 꺼낸다."""
    identity = getattr(request.state, "identity", None)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=UNAUTHENTICATED_DETAIL
        )
    return identity


def require_admin(identity: Identity = Depends(current_identity)) -> Identity:
    """admin 전용 라우트. 전역 규칙(viewer=GET 만)과 별개로 GET 도 막을 때 쓴다."""
    if not identity.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin 계정만 사용할 수 있습니다."
        )
    return identity


__all__ = [
    "FORBIDDEN_DETAIL",
    "PUBLIC_API_PATHS",
    "READ_METHODS",
    "UNAUTHENTICATED_DETAIL",
    "current_identity",
    "enforce_api_auth",
    "identity_from_request",
    "require_admin",
    "session_token",
]
