"""`POST /api/llm-connections/models` — 프로바이더 모델 목록 (SQLite + SDK mock).

Phase 4 — 1 차 사용 피드백: 모델명을 **외워서 타이핑**해야 했다.

트랙 간 계약:

    POST /api/llm-connections/models
    {connection_id?, provider?, api_key?, base_url?}
    -> { "provider": str, "models": [str, ...] }   (실패 시 502/400, detail 에 사유)

조회지만 GET 이 아니다 — `api_key` 를 쿼리스트링으로 받으면 평문 키가 서버 액세스
로그·프록시 로그·브라우저 히스토리에 남는다. 비밀은 바디로만 받는다.

프론트는 실패하면 자유 입력으로 폴백하므로 **detail 에 원인이 남는 것**까지가 계약이다.
그리고 어떤 경로로도 평문 API 키가 응답에 실리지 않아야 한다 — 오류 문구까지 포함해서다.

DB / TestClient fixture 와 SDK 대역은 `tests/test_llm_connections_api.py` 것을 그대로 쓴다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_llm_connections_api import (  # noqa: F401 - fixture 재수출
    API_KEY,
    FakeOpenAIError,
    client,
    create_payload,
    session_factory,
)


class FakeModelsAPI:
    def __init__(self, ids: list[str] | None = None, error: Exception | None = None) -> None:
        self.ids = ids or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return type(
            "Page", (), {"data": [type("Model", (), {"id": model})() for model in self.ids]}
        )()


class FakeModelsClient:
    def __init__(self, ids: list[str] | None = None, error: Exception | None = None) -> None:
        self.models = FakeModelsAPI(ids, error)


@pytest.fixture
def install_model_list(monkeypatch: pytest.MonkeyPatch):
    """`models.list()` 만 응답하는 SDK 클라이언트를 두 프로바이더 모두에 심는다."""
    created: dict[str, Any] = {}

    def _install(
        ids: list[str] | None = None, error: Exception | None = None
    ) -> FakeModelsClient:
        fake = FakeModelsClient(ids, error)

        def _factory(**kwargs: Any) -> FakeModelsClient:
            created["kwargs"] = kwargs
            return fake

        monkeypatch.setattr("app.llm_providers.openai_provider.OpenAI", _factory)
        monkeypatch.setattr("app.llm_providers.anthropic_provider.Anthropic", _factory)
        fake.created = created  # type: ignore[attr-defined]
        return fake

    return _install


# ------------------------------------------------------------------ 성공 경로


def test_models_returns_provider_and_model_ids(client: TestClient, install_model_list) -> None:
    install_model_list(["gpt-4o-mini", "gpt-4o"])

    response = client.post("/api/llm-connections/models", json={"provider": "openai"})

    assert response.status_code == 200, response.text
    assert response.json() == {"provider": "openai", "models": ["gpt-4o-mini", "gpt-4o"]}


def test_models_is_not_reachable_over_get(client: TestClient) -> None:
    """GET 은 없어야 한다 — 쿼리스트링에 실린 평문 키가 액세스 로그에 남는 경로다."""
    response = client.get("/api/llm-connections/models", params={"provider": "openai"})

    assert response.status_code in (404, 405, 422), response.text


def test_models_route_is_not_shadowed_by_the_id_route(
    client: TestClient, install_model_list
) -> None:
    """`/models` 가 `/{connection_id}` 아래로 내려가면 422 가 난다."""
    install_model_list(["gpt-4o"])

    response = client.post("/api/llm-connections/models", json={"provider": "openai"})

    assert response.status_code == 200, response.text


def test_models_uses_the_stored_connection_secret(
    client: TestClient, install_model_list
) -> None:
    client.post(
        "/api/llm-connections", json=create_payload(base_url="https://gateway.example/v1")
    )
    fake = install_model_list(["gpt-4o"])

    response = client.post("/api/llm-connections/models", json={"connection_id": 1})

    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "openai"
    # 저장된 암호문이 아니라 복호화된 평문이 SDK 로 간다.
    assert fake.created["kwargs"]["api_key"] == API_KEY
    assert fake.created["kwargs"]["base_url"] == "https://gateway.example/v1"


def test_models_body_values_win_over_the_stored_connection(
    client: TestClient, install_model_list
) -> None:
    """저장 전에 키를 바꿔 보는 흐름 — `/test` 와 같은 규칙이다."""
    client.post("/api/llm-connections", json=create_payload())
    fake = install_model_list(["claude-newest"])

    response = client.post(
        "/api/llm-connections/models",
        json={"connection_id": 1, "provider": "anthropic", "api_key": "sk-ant-other"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "anthropic"
    assert fake.created["kwargs"]["api_key"] == "sk-ant-other"


def test_models_accepts_null_and_omitted_optional_fields(
    client: TestClient, install_model_list
) -> None:
    """"지정 안 함"은 null 이든 생략이든 같다 — 폴백이 원인 없이 켜지면 안 된다."""
    install_model_list(["gpt-4o"])

    explicit_null = client.post(
        "/api/llm-connections/models",
        json={"provider": "openai", "connection_id": None, "api_key": None, "base_url": None},
    )
    omitted = client.post("/api/llm-connections/models", json={"provider": "openai"})

    assert explicit_null.status_code == 200, explicit_null.text
    assert explicit_null.json()["models"] == ["gpt-4o"]
    assert omitted.json() == explicit_null.json()


def test_models_openai_compatible_with_base_url_succeeds(
    client: TestClient, install_model_list
) -> None:
    fake = install_model_list(["llm-mock-1"])

    response = client.post(
        "/api/llm-connections/models",
        json={"provider": "openai_compatible", "base_url": "http://llm-mock:8000/v1"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"provider": "openai_compatible", "models": ["llm-mock-1"]}
    assert fake.created["kwargs"]["base_url"] == "http://llm-mock:8000/v1"


# ------------------------------------------------------------------ 실패 경로


def test_models_with_a_non_numeric_connection_id_is_rejected(client: TestClient) -> None:
    """바디에서는 타입 검증이 pydantic 몫이다 (422) — 프론트는 숫자만 보낸다."""
    response = client.post("/api/llm-connections/models", json={"connection_id": "abc"})

    assert response.status_code == 422, response.text
    assert "connection_id" in response.text


def test_models_without_provider_or_connection_is_400(client: TestClient) -> None:
    response = client.post("/api/llm-connections/models", json={})

    assert response.status_code == 400, response.text
    assert "provider" in response.json()["detail"]


def test_models_with_unsupported_provider_is_400(client: TestClient) -> None:
    """열거형이 아니라 str 로 받는다 — 422 는 프론트가 "경로 없음"으로 오해한다."""
    response = client.post("/api/llm-connections/models", json={"provider": "gemini"})

    assert response.status_code == 400, response.text
    assert "지원하지 않는 provider" in response.json()["detail"]


def test_models_openai_compatible_requires_base_url(client: TestClient) -> None:
    response = client.post(
        "/api/llm-connections/models", json={"provider": "openai_compatible"}
    )

    assert response.status_code == 400, response.text
    assert "base_url" in response.json()["detail"]


def test_models_unknown_connection_is_404(client: TestClient) -> None:
    response = client.post("/api/llm-connections/models", json={"connection_id": 42})

    assert response.status_code == 404, response.text


def test_models_provider_failure_is_502_with_a_reason(
    client: TestClient, install_model_list
) -> None:
    install_model_list(error=FakeOpenAIError("invalid api key", status_code=401))

    response = client.post(
        "/api/llm-connections/models", json={"provider": "openai", "api_key": "sk-bad"}
    )

    assert response.status_code == 502, response.text
    assert "invalid api key" in response.json()["detail"]


# ------------------------------------------------------- 평문 키 유출 차단


def test_models_error_detail_never_leaks_the_plaintext_key(
    client: TestClient, install_model_list
) -> None:
    """SDK 가 오류에 요청 문맥을 담는 구현이 있다 — detail 은 마스킹을 거친다."""
    install_model_list(error=FakeOpenAIError(f"401 for key {API_KEY}", status_code=401))

    response = client.post(
        "/api/llm-connections/models", json={"provider": "openai", "api_key": API_KEY}
    )

    assert response.status_code == 502, response.text
    assert API_KEY not in response.text


def test_models_error_detail_never_leaks_url_credentials(
    client: TestClient, install_model_list
) -> None:
    install_model_list(
        error=FakeOpenAIError("cannot reach https://admin:hunter2@gw.example/v1")
    )

    response = client.post(
        "/api/llm-connections/models",
        json={"provider": "openai_compatible", "base_url": "https://gw.example/v1"},
    )

    assert response.status_code == 502, response.text
    assert "hunter2" not in response.text


def test_models_response_carries_no_key_field(
    client: TestClient, install_model_list
) -> None:
    client.post("/api/llm-connections", json=create_payload())
    install_model_list(["gpt-4o"])

    response = client.post("/api/llm-connections/models", json={"connection_id": 1})

    assert API_KEY not in response.text
    # 마스킹된 값조차 싣지 않는다 — 응답은 provider·models 뿐이다.
    assert set(response.json()) == {"provider", "models"}
