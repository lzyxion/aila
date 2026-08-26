"""`/api/auth` 라우터.

계약:
- `POST /auth/login`  {username, password} → 200 {username, role} + httpOnly 세션 쿠키 / 실패 401
- `POST /auth/logout` → 204 (세션 무효화 — 서버 행 삭제 + 쿠키 만료)
- `GET  /auth/me`     → 200 {username, role} | 미인증 401
- `POST /auth/users`  {username, password, role} → 201 {username, role} (admin 전용)
- `GET  /auth/users`  → 200 {total, items} (admin 전용 — viewer 도 GET 이 막힌다)
- `PATCH /auth/users/{id}` {role?, active?, password?} → 200 UserDetail (admin 전용)
- `DELETE /auth/users/{id}` → 200 UserDetail (**비활성화**, 실삭제가 아니다)

login·logout 만 전역 인증 의존성에서 제외된다 (`auth.dependencies.PUBLIC_API_PATHS`).
`me` 가 제외되지 않는 것은 의도다 — 미인증이면 401 이 나와야 프런트가 `/login` 으로
보낼 판단을 할 수 있다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.auth import service
from app.auth.dependencies import current_identity, require_admin, session_token
from app.auth.service import Identity
from app.config import get_settings
from app.db import get_db
from app.schemas.api import (
    LoginRequest,
    UserCreateRequest,
    UserDetail,
    UserListResponse,
    UserRead,
    UserUpdateRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_CREDENTIALS = "아이디 또는 비밀번호가 올바르지 않습니다."


def _set_session_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    """세션 쿠키를 심는다.

    `httponly=True` — JS 가 읽을 수 없어야 XSS 로 세션이 통째로 빠져나가지 않는다.
    `samesite="lax"` — 타 사이트에서 시작된 POST 에 쿠키가 실리지 않으므로 CSRF 로
    쓰기 요청이 나가는 경로가 막힌다 (GET 내비게이션은 허용되어 로그인 후 링크 진입은 된다).
    `secure` 는 설정값이다 — 로컬 데모는 http 라 기본 False, 외부 노출 시 반드시 True.
    """
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


@router.post("/login", response_model=UserRead)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> UserRead:
    user = service.authenticate(db, payload.username, payload.password)
    if user is None:
        # 계정 부재와 비밀번호 오류를 구분해 주지 않는다 (계정 이름 열거 방지).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS
        )
    token, expires_at = service.create_session(db, user)
    _set_session_cookie(response, token, int(service.session_ttl().total_seconds()))
    return UserRead(username=user.username, role=user.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    """세션 무효화. 쿠키가 없거나 이미 죽은 세션이어도 204 다 (멱등).

    쿠키 삭제만으로는 부족하다 — 유출된 토큰이 남은 수명 동안 계속 유효해진다.
    그래서 서버의 세션 행을 **지운 뒤** 쿠키를 만료시킨다.
    """
    settings = get_settings()
    service.revoke_session(db, session_token(request))
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )


@router.get("/me", response_model=UserRead)
def me(identity: Identity = Depends(current_identity)) -> UserRead:
    return UserRead(username=identity.username, role=identity.role)


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest,
    db: Session = Depends(get_db),
    _admin: Identity = Depends(require_admin),
) -> UserRead:
    """viewer(또는 추가 admin) 계정 생성."""
    user = service.create_user(
        db, username=payload.username, password=payload.password, role=payload.role.value
    )
    return UserRead(username=user.username, role=user.role)


@router.get("/users", response_model=UserListResponse)
def list_users(
    db: Session = Depends(get_db),
    _admin: Identity = Depends(require_admin),
) -> UserListResponse:
    """계정 목록 (admin 전용).

    전역 규칙은 "viewer 도 GET 은 된다" 지만 계정 목록은 예외다 — 사용자 이름
    목록은 로그인 시도의 절반(계정 열거)이라 읽기도 admin 으로 막는다.
    """
    users = service.list_users(db)
    return UserListResponse(
        total=len(users), items=[UserDetail.model_validate(user) for user in users]
    )


@router.patch("/users/{user_id}", response_model=UserDetail)
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    admin: Identity = Depends(require_admin),
) -> UserDetail:
    """역할·활성 여부·비밀번호 수정 (admin 전용).

    마지막 남은 활성 admin 의 강등·비활성은 409, 자기 자신 비활성도 409 다.
    `active=false` 와 비밀번호 변경은 그 계정의 세션을 전부 무효화한다 — 자기
    비밀번호를 바꾸면 자기 세션도 끊긴다(의도: 유출을 전제로 바꾸는 조작이다).
    """
    user = service.update_user(
        db,
        user_id,
        role=payload.role.value if payload.role is not None else None,
        active=payload.active,
        password=payload.password,
        actor_id=admin.user_id,
    )
    return UserDetail.model_validate(user)


@router.delete("/users/{user_id}", response_model=UserDetail)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: Identity = Depends(require_admin),
) -> UserDetail:
    """계정 비활성화 (admin 전용). **행을 지우지 않는다** — 이력의 참조가 끊긴다.

    세션은 함께 무효화된다. 자기 자신과 마지막 관리자는 409 로 막힌다.
    """
    user = service.deactivate_user(db, user_id, actor_id=admin.user_id)
    return UserDetail.model_validate(user)


__all__ = ["router"]
