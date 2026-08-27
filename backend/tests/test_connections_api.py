"""`/api/loki-connections` 라우터 테스트 (SQLite in-memory + respx).

핵심 계약은 하나다 — **평문 secret 이 DB 에도 응답에도 남지 않는다.**
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.crypto import decrypt
from app.db import Base, get_db
from app.main import create_app
from app.models import LokiConnection

BASE_URL = "http://loki.test:3100"
READY_URL = f"{BASE_URL}/ready"
LABELS_URL = f"{BASE_URL}/loki/api/v1/labels"

SECRET = "tok-super-secret"


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    api = create_app()

    def override_get_db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    api.dependency_overrides[get_db] = override_get_db
    with TestClient(api) as test_client:
        yield test_client
    api.dependency_overrides.clear()


def create_payload(**overrides) -> dict:
    payload = {
        "name": "loki-local",
        "source_type": "loki",
        "base_url": BASE_URL,
        "auth_type": "bearer",
        "secret": SECRET,
        "label_mapping": {"service": "app"},
        "active": True,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------- CRUD


def test_create_encrypts_secret_and_never_returns_plaintext(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    response = client.post("/api/loki-connections", json=create_payload())
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["has_secret"] is True
    assert "secret" not in body
    assert SECRET not in response.text

    with session_factory() as session:
        stored = session.execute(select(LokiConnection)).scalar_one()
    assert stored.encrypted_secret != SECRET
    assert decrypt(stored.encrypted_secret) == SECRET


# --------------------------------------------- expected_services (Phase 7)


def test_expected_services_are_normalised_on_create(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    """공백·빈 값·중복이 그대로 저장되면 부재 경고 메시지가 그것을 그대로 되뱉는다."""
    body = client.post(
        "/api/loki-connections",
        json=create_payload(
            expected_services=[" payment-api ", "", "auth-api", "payment-api", "   "]
        ),
    ).json()

    # 순서는 운영자가 적은 그대로 유지한다 (정렬하면 매번 다르게 보인다).
    assert body["expected_services"] == ["payment-api", "auth-api"]
    with session_factory() as session:
        stored = session.execute(select(LokiConnection)).scalar_one()
    assert stored.expected_services == ["payment-api", "auth-api"]


def test_expected_services_default_to_empty(client: TestClient) -> None:
    """컬럼은 nullable 이다 — 응답에서는 "설정 안 함"이 빈 목록으로 보인다."""
    body = client.post("/api/loki-connections", json=create_payload()).json()

    assert body["expected_services"] == []


def test_expected_services_patch_sets_and_clears(client: TestClient) -> None:
    created = client.post(
        "/api/loki-connections", json=create_payload(expected_services=["payment-api"])
    ).json()

    updated = client.patch(
        f"/api/loki-connections/{created['id']}",
        json={"expected_services": ["cart-api", " cart-api ", "auth-api"]},
    ).json()
    assert updated["expected_services"] == ["cart-api", "auth-api"]

    # 키를 주지 않으면 그대로 둔다 (None = 변경 없음).
    kept = client.patch(
        f"/api/loki-connections/{created['id']}", json={"active": True}
    ).json()
    assert kept["expected_services"] == ["cart-api", "auth-api"]

    # 빈 목록은 "수집 중단 확인 끄기" 라는 의사표시다.
    cleared = client.patch(
        f"/api/loki-connections/{created['id']}", json={"expected_services": []}
    ).json()
    assert cleared["expected_services"] == []


def test_create_rejects_duplicate_name(client: TestClient) -> None:
    assert client.post("/api/loki-connections", json=create_payload()).status_code == 201
    duplicate = client.post("/api/loki-connections", json=create_payload(base_url="http://x:3100"))
    assert duplicate.status_code == 409


def test_list_and_get_roundtrip(client: TestClient) -> None:
    created = client.post("/api/loki-connections", json=create_payload()).json()

    listed = client.get("/api/loki-connections")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [created["id"]]
    assert SECRET not in listed.text

    fetched = client.get(f"/api/loki-connections/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["label_mapping"] == {"service": "app"}


def test_get_unknown_connection_returns_404(client: TestClient) -> None:
    assert client.get("/api/loki-connections/999").status_code == 404


def test_patch_updates_fields_and_reencrypts_secret(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    created = client.post("/api/loki-connections", json=create_payload()).json()

    response = client.patch(
        f"/api/loki-connections/{created['id']}",
        json={"name": "loki-prod", "secret": "new-token", "label_mapping": {"level": "severity"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "loki-prod"
    assert body["has_secret"] is True
    assert "new-token" not in response.text

    with session_factory() as session:
        stored = session.get(LokiConnection, created["id"])
        assert decrypt(stored.encrypted_secret) == "new-token"
        assert stored.label_mapping == {"level": "severity"}


def test_patch_with_explicit_null_secret_clears_it(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    created = client.post("/api/loki-connections", json=create_payload()).json()

    response = client.patch(f"/api/loki-connections/{created['id']}", json={"secret": None})
    assert response.status_code == 200
    assert response.json()["has_secret"] is False

    with session_factory() as session:
        assert session.get(LokiConnection, created["id"]).encrypted_secret is None


def test_patch_without_secret_key_keeps_stored_secret(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    created = client.post("/api/loki-connections", json=create_payload()).json()

    response = client.patch(f"/api/loki-connections/{created['id']}", json={"name": "renamed"})
    assert response.status_code == 200
    assert response.json()["has_secret"] is True

    with session_factory() as session:
        assert decrypt(session.get(LokiConnection, created["id"]).encrypted_secret) == SECRET


def test_delete_deactivates_instead_of_removing(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    created = client.post("/api/loki-connections", json=create_payload()).json()

    assert client.delete(f"/api/loki-connections/{created['id']}").status_code == 204

    with session_factory() as session:
        stored = session.get(LokiConnection, created["id"])
    assert stored is not None
    assert stored.active is False


# ------------------------------------------------------------------ /test


@respx.mock
def test_connection_test_with_unsaved_values(client: TestClient) -> None:
    respx.get(READY_URL).mock(return_value=httpx.Response(200, text="ready"))
    labels = respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"status": "success", "data": ["app"]})
    )

    response = client.post(
        "/api/loki-connections/test",
        json={"base_url": BASE_URL, "auth_type": "bearer", "secret": "tok-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["latency_ms"] is not None
    assert labels.calls.last.request.headers["authorization"] == "Bearer tok-1"


@respx.mock
def test_connection_test_with_saved_id_uses_stored_secret(client: TestClient) -> None:
    created = client.post("/api/loki-connections", json=create_payload()).json()
    respx.get(READY_URL).mock(return_value=httpx.Response(200, text="ready"))
    labels = respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"status": "success", "data": ["app", "env"]})
    )

    response = client.post("/api/loki-connections/test", json={"connection_id": created["id"]})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert labels.calls.last.request.headers["authorization"] == f"Bearer {SECRET}"


@respx.mock
def test_connection_test_reports_failure_without_raising(client: TestClient) -> None:
    respx.get(READY_URL).mock(return_value=httpx.Response(200, text="ready"))
    respx.get(LABELS_URL).mock(return_value=httpx.Response(403, text="forbidden"))

    response = client.post(
        "/api/loki-connections/test", json={"base_url": BASE_URL, "auth_type": "none"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "403" in body["message"]


@respx.mock
def test_connection_test_reports_unreachable_host(client: TestClient) -> None:
    respx.get(READY_URL).mock(side_effect=httpx.ConnectError("refused"))

    response = client.post("/api/loki-connections/test", json={"base_url": BASE_URL})

    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_connection_test_requires_id_or_base_url(client: TestClient) -> None:
    assert client.post("/api/loki-connections/test", json={}).status_code == 422


def test_connection_test_with_unknown_id_returns_404(client: TestClient) -> None:
    assert client.post("/api/loki-connections/test", json={"connection_id": 42}).status_code == 404


# ------------------------------------------------------------------- /labels


@respx.mock
def test_labels_endpoint_returns_names_and_values(client: TestClient) -> None:
    created = client.post("/api/loki-connections", json=create_payload()).json()
    respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"status": "success", "data": ["app", "env"]})
    )
    respx.get(f"{BASE_URL}/loki/api/v1/label/app/values").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": ["payment-api"]})
    )
    respx.get(f"{BASE_URL}/loki/api/v1/label/env/values").mock(
        return_value=httpx.Response(200, json={"status": "success", "data": ["prod", "staging"]})
    )

    response = client.get(f"/api/loki-connections/{created['id']}/labels")

    assert response.status_code == 200
    body = response.json()
    assert body["supports_label_discovery"] is True
    assert body["labels"] == ["app", "env"]
    assert body["values"]["env"] == ["prod", "staging"]


@respx.mock
def test_labels_endpoint_maps_source_failure_to_502(client: TestClient) -> None:
    created = client.post("/api/loki-connections", json=create_payload()).json()
    respx.get(LABELS_URL).mock(return_value=httpx.Response(500, text="boom"))

    response = client.get(f"/api/loki-connections/{created['id']}/labels")

    assert response.status_code == 502
