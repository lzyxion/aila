"""`/api/llm-connections` 라우터 테스트 (SQLite in-memory + SDK mock).

핵심 계약은 셋이다.

1. 평문 api_key 가 **DB 에도 응답에도** 남지 않는다 (암호화 저장 + 마스킹 표시).
2. `is_default=True` 인 연결은 **항상 최대 하나**다.
3. `DELETE` 는 삭제가 아니라 `active=false` 비활성화다.

`/test` 는 실제 과금 호출이므로 SDK 클라이언트를 대체해 확인한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openai import OpenAIError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.crypto import decrypt
from app.db import Base, get_db
from app.main import create_app
from app.models import LLMConnection

API_KEY = "sk-super-secret-key-1234"


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
        "name": "openai-default",
        "provider": "openai",
        "model": "gpt-test",
        "base_url": None,
        "api_key": API_KEY,
        "is_default": True,
        "active": True,
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------- OpenAI SDK 대체


class FakeOpenAIError(OpenAIError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeCompletions:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        message = type("Message", (), {"content": "ok", "refusal": None})()
        choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
        usage = type("Usage", (), {"prompt_tokens": 3, "completion_tokens": 1})()
        return type("Response", (), {"choices": [choice], "usage": usage})()


class FakeOpenAIClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.completions = FakeCompletions(error)
        self.chat = type("Chat", (), {"completions": self.completions})()


@pytest.fixture
def install_openai(monkeypatch: pytest.MonkeyPatch):
    created: dict[str, Any] = {}

    def _install(error: Exception | None = None) -> FakeOpenAIClient:
        fake = FakeOpenAIClient(error)

        def _factory(**kwargs: Any) -> FakeOpenAIClient:
            created["kwargs"] = kwargs
            return fake

        monkeypatch.setattr("app.llm_providers.openai_provider.OpenAI", _factory)
        fake.created = created  # type: ignore[attr-defined]
        return fake

    return _install


# ---------------------------------------------------------------------- CRUD


def test_create_encrypts_api_key_and_never_returns_plaintext(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    response = client.post("/api/llm-connections", json=create_payload())
    assert response.status_code == 201, response.text

    body = response.json()
    assert API_KEY not in response.text
    assert body["api_key_masked"].endswith(API_KEY[-4:])
    assert body["api_key_masked"].startswith("*")
    assert "api_key" not in body or body.get("api_key") is None

    with session_factory() as session:
        stored = session.execute(select(LLMConnection)).scalar_one()
    assert stored.encrypted_api_key != API_KEY
    assert decrypt(stored.encrypted_api_key) == API_KEY


def test_create_rejects_duplicate_name(client: TestClient) -> None:
    assert client.post("/api/llm-connections", json=create_payload()).status_code == 201
    duplicate = client.post("/api/llm-connections", json=create_payload(model="gpt-other"))
    assert duplicate.status_code == 409


def test_create_requires_provider_and_model(client: TestClient) -> None:
    assert client.post("/api/llm-connections", json={"name": "x"}).status_code == 422


def test_create_rejects_unknown_provider_value(client: TestClient) -> None:
    payload = create_payload(provider="gemini")
    assert client.post("/api/llm-connections", json=payload).status_code == 422


def test_list_and_get_roundtrip(client: TestClient) -> None:
    created = client.post("/api/llm-connections", json=create_payload()).json()

    listed = client.get("/api/llm-connections")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [created["id"]]
    assert API_KEY not in listed.text

    fetched = client.get(f"/api/llm-connections/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["provider"] == "openai"
    assert API_KEY not in fetched.text


def test_get_unknown_connection_returns_404(client: TestClient) -> None:
    assert client.get("/api/llm-connections/999").status_code == 404


def test_connection_without_key_reports_null_mask(client: TestClient) -> None:
    body = client.post("/api/llm-connections", json=create_payload(api_key=None)).json()
    assert body["api_key_masked"] is None


def test_patch_updates_fields_and_reencrypts_key(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    created = client.post("/api/llm-connections", json=create_payload()).json()

    response = client.patch(
        f"/api/llm-connections/{created['id']}",
        json={"name": "openai-prod", "model": "gpt-new", "api_key": "sk-rotated-key-9876"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "openai-prod"
    assert body["model"] == "gpt-new"
    assert "sk-rotated-key-9876" not in response.text

    with session_factory() as session:
        stored = session.get(LLMConnection, created["id"])
    assert decrypt(stored.encrypted_api_key) == "sk-rotated-key-9876"


def test_patch_with_explicit_null_key_clears_it(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    created = client.post("/api/llm-connections", json=create_payload()).json()

    response = client.patch(f"/api/llm-connections/{created['id']}", json={"api_key": None})
    assert response.status_code == 200
    assert response.json()["api_key_masked"] is None

    with session_factory() as session:
        assert session.get(LLMConnection, created["id"]).encrypted_api_key is None


def test_patch_without_key_keeps_stored_key(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    created = client.post("/api/llm-connections", json=create_payload()).json()

    response = client.patch(f"/api/llm-connections/{created['id']}", json={"model": "gpt-new"})
    assert response.status_code == 200

    with session_factory() as session:
        assert decrypt(session.get(LLMConnection, created["id"]).encrypted_api_key) == API_KEY


def test_patch_rejects_duplicate_name(client: TestClient) -> None:
    first = client.post("/api/llm-connections", json=create_payload()).json()
    client.post(
        "/api/llm-connections",
        json=create_payload(name="second", is_default=False),
    )

    response = client.patch(f"/api/llm-connections/{first['id']}", json={"name": "second"})
    assert response.status_code == 409


def test_delete_deactivates_instead_of_removing(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    created = client.post("/api/llm-connections", json=create_payload()).json()

    assert client.delete(f"/api/llm-connections/{created['id']}").status_code == 204

    with session_factory() as session:
        stored = session.get(LLMConnection, created["id"])
    assert stored is not None
    assert stored.active is False
    # 비활성 연결이 기본으로 남으면 분석이 죽은 연결을 고른다.
    assert stored.is_default is False


# ------------------------------------------------------------ is_default 단일성


def _defaults(session_factory: sessionmaker[Session]) -> list[int]:
    with session_factory() as session:
        rows = session.execute(
            select(LLMConnection.id).where(LLMConnection.is_default.is_(True))
        ).scalars()
        return list(rows)


def test_creating_second_default_clears_the_first(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    first = client.post("/api/llm-connections", json=create_payload()).json()
    second = client.post(
        "/api/llm-connections",
        json=create_payload(name="anthropic-default", provider="anthropic", model="claude-test"),
    ).json()

    assert second["is_default"] is True
    assert _defaults(session_factory) == [second["id"]]
    assert client.get(f"/api/llm-connections/{first['id']}").json()["is_default"] is False


def test_patching_default_moves_it(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    first = client.post("/api/llm-connections", json=create_payload()).json()
    second = client.post(
        "/api/llm-connections",
        json=create_payload(name="second", is_default=False),
    ).json()

    response = client.patch(f"/api/llm-connections/{second['id']}", json={"is_default": True})
    assert response.status_code == 200
    assert response.json()["is_default"] is True
    assert _defaults(session_factory) == [second["id"]]
    assert client.get(f"/api/llm-connections/{first['id']}").json()["is_default"] is False


def test_deactivating_via_patch_drops_default_flag(
    client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    created = client.post("/api/llm-connections", json=create_payload()).json()

    response = client.patch(f"/api/llm-connections/{created['id']}", json={"active": False})

    assert response.status_code == 200
    assert response.json()["is_default"] is False
    assert _defaults(session_factory) == []


# ------------------------------------------------------------------- /test


def test_test_with_unsaved_values_calls_provider(client: TestClient, install_openai) -> None:
    fake = install_openai()

    response = client.post(
        "/api/llm-connections/test",
        json={"provider": "openai", "model": "gpt-test", "api_key": "sk-unsaved"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["latency_ms"] is not None
    # 연결 테스트도 과금이다 — 최소 토큰만 쓴다.
    assert fake.completions.calls[0]["max_completion_tokens"] == 1
    assert fake.created["kwargs"]["api_key"] == "sk-unsaved"


def test_test_with_saved_id_uses_stored_key(client: TestClient, install_openai) -> None:
    created = client.post("/api/llm-connections", json=create_payload()).json()
    fake = install_openai()

    response = client.post("/api/llm-connections/test", json={"connection_id": created["id"]})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    # 저장된 암호문이 아니라 복호화된 평문으로 호출한다.
    assert fake.created["kwargs"]["api_key"] == API_KEY


def test_test_reports_failure_without_raising(client: TestClient, install_openai) -> None:
    install_openai(FakeOpenAIError("invalid api key", status_code=401))

    response = client.post(
        "/api/llm-connections/test",
        json={"provider": "openai", "model": "gpt-test", "api_key": "sk-bad"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "401" in body["message"]


def test_test_requires_id_or_provider_and_model(client: TestClient) -> None:
    assert client.post("/api/llm-connections/test", json={}).status_code == 422
    assert (
        client.post("/api/llm-connections/test", json={"provider": "openai"}).status_code == 422
    )


def test_test_with_unknown_id_returns_404(client: TestClient) -> None:
    assert client.post("/api/llm-connections/test", json={"connection_id": 42}).status_code == 404


def test_openai_compatible_maps_to_openai_adapter() -> None:
    """`openai_compatible` 은 OpenAI 어댑터로 매핑된다 (base_url 필수)."""
    from app.llm_providers.factory import build_llm_provider_from_values
    from app.llm_providers.openai_provider import OpenAIProvider

    provider = build_llm_provider_from_values(
        provider="openai_compatible",
        model="qwen2.5",
        api_key="k",
        base_url="http://vllm:8000/v1",
    )
    assert isinstance(provider, OpenAIProvider)

    with pytest.raises(ValueError, match="base_url"):
        build_llm_provider_from_values(provider="openai_compatible", model="qwen2.5")


def test_test_response_never_contains_plaintext_key(client: TestClient, install_openai) -> None:
    client.post("/api/llm-connections", json=create_payload())
    install_openai()

    response = client.post("/api/llm-connections/test", json={"connection_id": 1})

    assert API_KEY not in response.text
