"""인증·권한 자체의 테스트 (Phase 5).

`tests/conftest.py` 는 모든 테스트 클라이언트에 admin 세션을 기본으로 깔아 준다.
이 모듈은 그 기본값에서 빠져나와(`@pytest.mark.real_auth`) **실제 경로**를 탄다 —
쿠키 발급·401·403·만료·로그아웃 무효화가 여기서만 검증된다.

DB / TestClient fixture 는 정책 API 트랙의 `tests/test_policies_fixtures.py` 것을
그대로 쓴다 (테이블은 `Base.metadata.create_all` 로 전부 만들어진다).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import service as auth_service
from app.auth.passwords import hash_password, verify_password
from app.config import get_settings
from app.enums import UserRole
from app.models import User, UserSession
from tests.test_policies_fixtures import (  # noqa: F401 - fixture 재수출
    client,
    db,
    engine,
    no_real_log_source,
    session_factory,
)

pytestmark = pytest.mark.real_auth

COOKIE = "aila_session"


def make_user(db, *, username: str, password: str, role: str, active: bool = True) -> User:
    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        active=active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login(client: TestClient, username: str, password: str):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# ------------------------------------------------------------------ 비밀번호


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("hunter2")
    second = hash_password("hunter2")
    assert first != second, "salt 가 없으면 같은 비밀번호가 같은 해시가 된다"
    assert first.startswith("scrypt$")
    assert "hunter2" not in first
    assert verify_password("hunter2", first)
    assert verify_password("hunter2", second)
    assert not verify_password("hunter3", first)


def test_verify_password_rejects_broken_records_without_raising() -> None:
    """깨진 행 하나로 로그인 경로가 500 을 내면 아무도 못 들어가고 원인도 안 보인다."""
    for broken in (None, "", "not-a-hash", "scrypt$x$y$z$q$w", "bcrypt$1$2$3$4$5"):
        assert verify_password("hunter2", broken) is False


# --------------------------------------------------------------- 로그인 계약


def test_login_returns_username_role_and_sets_httponly_cookie(client, db) -> None:
    make_user(db, username="alice", password="s3cret", role=UserRole.ADMIN.value)

    response = login(client, "alice", "s3cret")

    assert response.status_code == 200
    assert response.json() == {"username": "alice", "role": "admin"}

    raw = response.headers["set-cookie"]
    assert "HttpOnly" in raw
    assert "SameSite=lax" in raw or "samesite=lax" in raw.lower()
    assert client.cookies.get(COOKIE)
    # 쿠키에 실린 값은 DB 에 그대로 저장되지 않는다 (해시만 남는다).
    stored = db.scalars(select(UserSession)).all()
    assert len(stored) == 1
    assert stored[0].token_hash != client.cookies.get(COOKIE)
    assert stored[0].token_hash == auth_service.hash_token(client.cookies.get(COOKIE))


def test_login_with_wrong_password_is_401_and_sets_no_cookie(client, db) -> None:
    make_user(db, username="alice", password="s3cret", role=UserRole.ADMIN.value)
    response = login(client, "alice", "wrong")
    assert response.status_code == 401
    assert response.json()["detail"]
    assert client.cookies.get(COOKIE) is None


def test_login_for_unknown_user_is_401_with_the_same_detail(client, db) -> None:
    """계정 부재와 비밀번호 오류를 구분해 주면 계정 이름을 열거할 수 있다."""
    make_user(db, username="alice", password="s3cret", role=UserRole.ADMIN.value)
    unknown = login(client, "nobody", "s3cret").json()["detail"]
    wrong = login(client, "alice", "nope").json()["detail"]
    assert unknown == wrong


def test_inactive_user_cannot_log_in(client, db) -> None:
    make_user(db, username="ghost", password="pw", role=UserRole.ADMIN.value, active=False)
    assert login(client, "ghost", "pw").status_code == 401


def test_me_returns_the_session_owner(client, db) -> None:
    make_user(db, username="viewy", password="pw", role=UserRole.VIEWER.value)
    login(client, "viewy", "pw")
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json() == {"username": "viewy", "role": "viewer"}


def test_me_without_a_session_is_401(client) -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert "detail" in response.json()


# ------------------------------------------------------------------ 로그아웃


def test_logout_invalidates_the_session_on_the_server(client, db) -> None:
    """쿠키를 지우는 것만으로는 부족하다 — 유출된 토큰이 남은 수명 동안 유효해진다."""
    make_user(db, username="alice", password="pw", role=UserRole.ADMIN.value)
    login(client, "alice", "pw")
    token = client.cookies.get(COOKIE)

    assert client.post("/api/auth/logout").status_code == 204
    assert db.scalars(select(UserSession)).all() == []

    # 쿠키를 손으로 되돌려도 통과하지 않는다 (서버 행이 사라졌으므로).
    client.cookies.set(COOKIE, token)
    assert client.get("/api/auth/me").status_code == 401


def test_logout_without_a_session_is_still_204(client) -> None:
    assert client.post("/api/auth/logout").status_code == 204


def test_expired_session_is_rejected_and_removed(client, db) -> None:
    make_user(db, username="alice", password="pw", role=UserRole.ADMIN.value)
    login(client, "alice", "pw")

    row = db.scalars(select(UserSession)).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.add(row)
    db.commit()

    assert client.get("/api/auth/me").status_code == 401
    assert db.scalars(select(UserSession)).all() == [], "만료 행은 판정 시점에 지워진다"


def test_session_expiry_follows_the_configured_ttl(client, db) -> None:
    make_user(db, username="alice", password="pw", role=UserRole.ADMIN.value)
    before = datetime.now(UTC)
    login(client, "alice", "pw")

    row = db.scalars(select(UserSession)).one()
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    expected = before + timedelta(hours=get_settings().session_ttl_hours)
    assert abs((expires - expected).total_seconds()) < 60


# -------------------------------------------------------------- 전역 보호


def test_every_api_route_requires_authentication(client) -> None:
    for method, path in (
        ("GET", "/api/policies"),
        ("GET", "/api/loki-connections"),
        ("GET", "/api/llm-connections"),
        ("GET", "/api/usage"),
        ("GET", "/api/dashboard/summary"),
        ("POST", "/api/policies"),
    ):
        response = client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code == 401, f"{method} {path}"
        assert response.json()["detail"]


def test_health_and_docs_stay_open(client) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


# ------------------------------------------------------------ viewer 권한


def test_viewer_may_read(client, db) -> None:
    make_user(db, username="viewy", password="pw", role=UserRole.VIEWER.value)
    login(client, "viewy", "pw")
    assert client.get("/api/policies").status_code == 200


def test_viewer_may_not_write(client, db) -> None:
    make_user(db, username="viewy", password="pw", role=UserRole.VIEWER.value)
    login(client, "viewy", "pw")

    for method, path in (
        ("POST", "/api/policies"),
        ("PATCH", "/api/policies/1"),
        ("DELETE", "/api/policies/1"),
        ("POST", "/api/loki-connections"),
    ):
        response = client.request(method, path, json={} if method != "DELETE" else None)
        assert response.status_code == 403, f"{method} {path}"
        assert response.json()["detail"]


def test_admin_may_write(client, db) -> None:
    make_user(db, username="alice", password="pw", role=UserRole.ADMIN.value)
    login(client, "alice", "pw")
    # 본문이 비어 422 로 떨어지더라도 **403 은 아니다** — 권한 게이트를 통과했다는 뜻.
    assert client.post("/api/policies", json={}).status_code == 422


def test_viewer_can_log_out(client, db) -> None:
    """로그아웃은 POST 지만 viewer 도 할 수 있어야 한다 (인증 예외 경로)."""
    make_user(db, username="viewy", password="pw", role=UserRole.VIEWER.value)
    login(client, "viewy", "pw")
    assert client.post("/api/auth/logout").status_code == 204


# ------------------------------------------------------------ 계정 생성 API


def test_admin_can_create_a_viewer_account(client, db) -> None:
    make_user(db, username="alice", password="pw", role=UserRole.ADMIN.value)
    login(client, "alice", "pw")

    response = client.post(
        "/api/auth/users", json={"username": "bob", "password": "pw2", "role": "viewer"}
    )
    assert response.status_code == 201
    assert response.json() == {"username": "bob", "role": "viewer"}

    created = db.scalar(select(User).where(User.username == "bob"))
    assert created is not None
    assert created.password_hash != "pw2" and "pw2" not in created.password_hash

    # 만든 계정으로 실제로 로그인된다.
    client.post("/api/auth/logout")
    assert login(client, "bob", "pw2").json()["role"] == "viewer"


def test_creating_a_duplicate_account_is_409(client, db) -> None:
    make_user(db, username="alice", password="pw", role=UserRole.ADMIN.value)
    login(client, "alice", "pw")
    body = {"username": "bob", "password": "pw2", "role": "viewer"}
    assert client.post("/api/auth/users", json=body).status_code == 201
    assert client.post("/api/auth/users", json=body).status_code == 409


def test_viewer_cannot_create_accounts(client, db) -> None:
    make_user(db, username="viewy", password="pw", role=UserRole.VIEWER.value)
    login(client, "viewy", "pw")
    response = client.post(
        "/api/auth/users", json={"username": "bob", "password": "pw2", "role": "admin"}
    )
    assert response.status_code == 403


def test_unknown_role_is_rejected(client, db) -> None:
    make_user(db, username="alice", password="pw", role=UserRole.ADMIN.value)
    login(client, "alice", "pw")
    response = client.post(
        "/api/auth/users", json={"username": "bob", "password": "pw2", "role": "superuser"}
    )
    assert response.status_code == 422


# ------------------------------------------------------------------ 시드


def test_seed_admin_creates_the_account_once(db) -> None:
    created = auth_service.seed_admin(db)
    assert created is not None
    assert created.role == UserRole.ADMIN.value
    assert auth_service.seed_admin(db) is None, "이미 있으면 아무것도 하지 않는다"


def test_seed_admin_does_not_reset_a_changed_password(db) -> None:
    """재기동마다 env 값으로 되돌리면 운영자가 바꾼 비밀번호가 기본값으로 돌아간다."""
    settings = get_settings()
    make_user(db, username=settings.admin_username, password="changed", role="admin")
    assert auth_service.seed_admin(db) is None
    user = db.scalar(select(User).where(User.username == settings.admin_username))
    assert verify_password("changed", user.password_hash)
