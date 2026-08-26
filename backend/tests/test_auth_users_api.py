"""계정 관리 API (Phase 6) — 목록·수정·비활성화.

이 모듈이 고정하는 것은 편의 기능이 아니라 **안전 규칙**이다.

- 마지막 남은 활성 admin 은 강등도 비활성도 할 수 없다 (409). 뚫리면 아무도 아무것도
  고칠 수 없는 DB 가 되고 복구 경로가 DB 직접 수정뿐이다.
- 자기 자신은 비활성화할 수 없다 (409).
- `active=false` 와 비밀번호 변경은 그 계정의 **세션을 전부 무효화**한다. 이것이 없으면
  "잠갔다" 고 표시되는 계정의 쿠키가 남은 12 시간 동안 그대로 통과한다.
- 계정 목록은 viewer 도 못 읽는다 (사용자 이름 목록은 계정 열거의 절반이다).

전부 `@pytest.mark.real_auth` 다 — 주체가 누구인지(자기 자신 판정)와 세션이 실제로
끊겼는지를 검증해야 하므로 conftest 의 기본 admin 세션에서 빠져나온다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.enums import UserRole
from app.models import User, UserSession
from tests.test_auth_api import COOKIE, login, make_user
from tests.test_policies_fixtures import (  # noqa: F401 - fixture 재수출
    client,
    db,
    engine,
    no_real_log_source,
    session_factory,
)

pytestmark = pytest.mark.real_auth


def as_admin(client: TestClient, db, *, username: str = "root") -> User:
    """관리자 계정을 만들고 그 세션으로 로그인한 상태를 만든다."""
    user = make_user(db, username=username, password="pw", role=UserRole.ADMIN.value)
    client.cookies.clear()
    assert login(client, username, "pw").status_code == 200
    return user


def sessions_of(db, user_id: int) -> list[UserSession]:
    db.expire_all()
    return list(db.scalars(select(UserSession).where(UserSession.user_id == user_id)).all())


# -------------------------------------------------------------------- 목록


def test_list_users_returns_envelope_with_status_fields(client, db) -> None:
    as_admin(client, db)
    make_user(db, username="watcher", password="pw", role=UserRole.VIEWER.value)
    make_user(db, username="gone", password="pw", role=UserRole.VIEWER.value, active=False)

    body = client.get("/api/auth/users").json()

    assert body["total"] == 3
    assert [item["username"] for item in body["items"]] == ["root", "watcher", "gone"]
    row = body["items"][1]
    assert row["role"] == "viewer"
    assert row["active"] is True
    assert row["created_at"]
    assert row["id"] > 0
    assert body["items"][2]["active"] is False
    # 해시는 어떤 경로로도 나가지 않는다.
    assert "password_hash" not in row and "password" not in row


def test_list_users_is_admin_only_even_though_it_is_a_get(client, db) -> None:
    """viewer 는 GET 이 열려 있지만 계정 목록만은 예외다 (계정 열거 방지)."""
    make_user(db, username="watcher", password="pw", role=UserRole.VIEWER.value)
    login(client, "watcher", "pw")

    assert client.get("/api/auth/users").status_code == 403


def test_user_endpoints_require_authentication(client, db) -> None:
    make_user(db, username="root", password="pw", role=UserRole.ADMIN.value)
    client.cookies.clear()

    assert client.get("/api/auth/users").status_code == 401
    assert client.patch("/api/auth/users/1", json={"active": False}).status_code == 401
    assert client.delete("/api/auth/users/1").status_code == 401


# -------------------------------------------------------------------- 수정


def test_viewer_can_still_read_the_new_phase6_read_endpoints(client, db) -> None:
    """계정 목록만 admin 전용이다 — 나머지 신규 조회는 전역 규칙(GET 허용) 그대로다."""
    make_user(db, username="watcher", password="pw", role=UserRole.VIEWER.value)
    login(client, "watcher", "pw")

    assert client.get("/api/dashboard/error-groups").status_code == 200
    assert client.get("/api/usage", params={"group_by": "day"}).status_code == 200
    assert client.get("/api/analysis-jobs", params={"q": "payment"}).status_code == 200


def test_patch_changes_role_and_returns_the_updated_row(client, db) -> None:
    as_admin(client, db)
    target = make_user(db, username="watcher", password="pw", role=UserRole.VIEWER.value)

    body = client.patch(f"/api/auth/users/{target.id}", json={"role": "admin"}).json()

    assert body["id"] == target.id
    assert body["username"] == "watcher"
    assert body["role"] == "admin"
    assert body["active"] is True
    db.expire_all()
    assert db.get(User, target.id).role == "admin"


def test_patch_with_no_fields_changes_nothing(client, db) -> None:
    as_admin(client, db)
    target = make_user(db, username="watcher", password="pw", role=UserRole.VIEWER.value)
    before = db.get(User, target.id).password_hash

    body = client.patch(f"/api/auth/users/{target.id}", json={}).json()

    assert body["role"] == "viewer" and body["active"] is True
    db.expire_all()
    assert db.get(User, target.id).password_hash == before


def test_patch_unknown_user_is_404(client, db) -> None:
    as_admin(client, db)
    response = client.patch("/api/auth/users/9999", json={"active": False})
    assert response.status_code == 404
    assert response.json()["detail"]


def test_viewer_cannot_modify_accounts(client, db) -> None:
    make_user(db, username="root", password="pw", role=UserRole.ADMIN.value)
    target = make_user(db, username="watcher", password="pw", role=UserRole.VIEWER.value)
    login(client, "watcher", "pw")

    assert client.patch(f"/api/auth/users/{target.id}", json={"role": "admin"}).status_code == 403
    assert client.delete(f"/api/auth/users/{target.id}").status_code == 403


# ------------------------------------------------------- 세션 무효화 (계약의 핵심)


def test_password_change_invalidates_that_accounts_sessions(client, db) -> None:
    """비밀번호를 바꾸는 이유의 절반은 "이미 유출됐다" 다 — 옛 쿠키가 살아 있으면 의미가 없다."""
    admin = as_admin(client, db)
    target = make_user(db, username="watcher", password="old-pw", role=UserRole.VIEWER.value)

    client.cookies.clear()
    login(client, "watcher", "old-pw")
    stale_cookie = client.cookies.get(COOKIE)
    assert client.get("/api/auth/me").status_code == 200

    client.cookies.clear()
    login(client, admin.username, "pw")
    assert (
        client.patch(
            f"/api/auth/users/{target.id}", json={"password": "new-pw"}
        ).status_code
        == 200
    )

    assert sessions_of(db, target.id) == []
    client.cookies.clear()
    client.cookies.set(COOKIE, stale_cookie)
    assert client.get("/api/auth/me").status_code == 401

    client.cookies.clear()
    assert login(client, "watcher", "old-pw").status_code == 401
    assert login(client, "watcher", "new-pw").status_code == 200


def test_password_change_stores_a_scrypt_hash_not_the_plaintext(client, db) -> None:
    as_admin(client, db)
    target = make_user(db, username="watcher", password="old-pw", role=UserRole.VIEWER.value)

    client.patch(f"/api/auth/users/{target.id}", json={"password": "new-pw"})

    db.expire_all()
    stored = db.get(User, target.id).password_hash
    assert stored.startswith("scrypt$")
    assert "new-pw" not in stored


def test_deactivation_invalidates_sessions_and_blocks_login(client, db) -> None:
    admin = as_admin(client, db)
    target = make_user(db, username="watcher", password="pw", role=UserRole.VIEWER.value)

    client.cookies.clear()
    login(client, "watcher", "pw")
    stale_cookie = client.cookies.get(COOKIE)

    client.cookies.clear()
    login(client, admin.username, "pw")
    body = client.patch(f"/api/auth/users/{target.id}", json={"active": False}).json()

    assert body["active"] is False
    assert sessions_of(db, target.id) == []
    client.cookies.clear()
    client.cookies.set(COOKIE, stale_cookie)
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.clear()
    assert login(client, "watcher", "pw").status_code == 401


def test_password_change_on_the_actor_ends_the_actors_own_session(client, db) -> None:
    """자기 비밀번호를 바꾸면 자기 세션도 끊긴다 — 유출을 전제로 하는 조작이라 의도다."""
    admin = as_admin(client, db)
    make_user(db, username="second", password="pw", role=UserRole.ADMIN.value)

    assert (
        client.patch(f"/api/auth/users/{admin.id}", json={"password": "new-pw"}).status_code
        == 200
    )

    assert sessions_of(db, admin.id) == []
    assert client.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------- 비활성화


def test_delete_deactivates_instead_of_deleting_the_row(client, db) -> None:
    admin = as_admin(client, db)
    target = make_user(db, username="watcher", password="pw", role=UserRole.VIEWER.value)
    client.cookies.clear()
    login(client, "watcher", "pw")
    client.cookies.clear()
    login(client, admin.username, "pw")

    body = client.delete(f"/api/auth/users/{target.id}").json()

    assert body["id"] == target.id
    assert body["active"] is False
    db.expire_all()
    # 실삭제가 아니다 — 행이 사라지면 그 계정이 남긴 이력의 참조가 끊긴다.
    assert db.get(User, target.id) is not None
    assert sessions_of(db, target.id) == []


def test_deleting_an_already_inactive_user_is_idempotent(client, db) -> None:
    as_admin(client, db)
    target = make_user(
        db, username="gone", password="pw", role=UserRole.VIEWER.value, active=False
    )

    assert client.delete(f"/api/auth/users/{target.id}").json()["active"] is False
    assert client.delete(f"/api/auth/users/{target.id}").json()["active"] is False


def test_cannot_deactivate_yourself(client, db) -> None:
    admin = as_admin(client, db)
    make_user(db, username="second", password="pw", role=UserRole.ADMIN.value)

    delete_response = client.delete(f"/api/auth/users/{admin.id}")
    patch_response = client.patch(f"/api/auth/users/{admin.id}", json={"active": False})

    assert delete_response.status_code == 409
    assert patch_response.status_code == 409
    db.expire_all()
    assert db.get(User, admin.id).active is True


# ------------------------------------------------- 마지막 관리자 보호 (409)


def test_last_active_admin_cannot_be_demoted(client, db) -> None:
    admin = as_admin(client, db)
    make_user(db, username="watcher", password="pw", role=UserRole.VIEWER.value)

    response = client.patch(f"/api/auth/users/{admin.id}", json={"role": "viewer"})

    assert response.status_code == 409
    assert response.json()["detail"]
    db.expire_all()
    assert db.get(User, admin.id).role == "admin"


def test_last_active_admin_cannot_be_deactivated_by_another_admin(client, db) -> None:
    """자기 자신 규칙이 아니라 **마지막 관리자** 규칙이 잡는 경우.

    두 번째 관리자로 로그인해 첫 관리자를 지우면, 그 시점에는 관리자가 둘이라
    통과한다. 그 다음이 문제다 — 남은 하나를 지우려 하면 409 여야 한다.
    """
    first = as_admin(client, db, username="first")
    second = make_user(db, username="second", password="pw", role=UserRole.ADMIN.value)
    client.cookies.clear()
    login(client, "second", "pw")

    assert client.delete(f"/api/auth/users/{first.id}").status_code == 200
    response = client.patch(f"/api/auth/users/{second.id}", json={"active": False})

    # 마지막 관리자를 자기 자신이 아닌 규칙으로도 막는지 보려면 대상이 남에게 있어야
    # 하지만, 남은 관리자가 하나뿐이면 그 하나가 곧 자기 자신이다. 두 규칙 모두 409 다.
    assert response.status_code == 409
    db.expire_all()
    assert db.get(User, second.id).active is True


def test_inactive_admins_do_not_count_as_the_remaining_admin(client, db) -> None:
    """비활성 admin 은 "관리자가 또 있다" 의 근거가 될 수 없다 (로그인 자체가 막힌다)."""
    admin = as_admin(client, db)
    make_user(db, username="retired", password="pw", role=UserRole.ADMIN.value, active=False)

    response = client.patch(f"/api/auth/users/{admin.id}", json={"role": "viewer"})

    assert response.status_code == 409


def test_demoting_an_admin_is_allowed_while_another_active_admin_remains(client, db) -> None:
    as_admin(client, db, username="first")
    second = make_user(db, username="second", password="pw", role=UserRole.ADMIN.value)

    body = client.patch(f"/api/auth/users/{second.id}", json={"role": "viewer"}).json()

    assert body["role"] == "viewer"
