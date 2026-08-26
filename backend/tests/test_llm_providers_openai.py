"""OpenAI 어댑터 테스트.

실제 API 는 절대 호출하지 않는다 — `openai.OpenAI` 클라이언트를 통째로 대체한다.
검증하는 계약은 네 가지다.

1. 요청에 structured outputs 스키마가 실린다 (`response_format.json_schema.strict`).
2. 응답은 **원시 dict 그대로** 돌아온다 (어댑터 안에서 Pydantic 검증하지 않는다).
3. SDK 예외·JSON 파싱 실패는 전부 `LLMError` 로 감싼다.
4. `test_connection()` 은 최소 토큰만 쓰고 예외를 던지지 않는다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from openai import OpenAIError

from app.llm_providers.openai_provider import OpenAIProvider
from app.providers.llm import LLMError, LLMPrompt

RAW_RESULT = {
    "summary": "결제 API 에서 DB 커넥션 풀 고갈",
    "severity": "high",
    "hypotheses": [{"cause": "풀 크기 부족", "confidence": 0.6, "evidence": ["timeout"]}],
    "investigation_steps": ["커넥션 수 확인"],
    "mitigation": ["풀 크기 상향"],
    "limitations": ["로그만으로는 트래픽 급증 여부를 알 수 없다"],
    # 스키마에 없는 키 — 어댑터가 검증하지 않는다는 증거로 일부러 넣는다.
    "unexpected_field": 1,
}


class FakeOpenAIError(OpenAIError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeMessage:
    def __init__(self, content: str | None = None, refusal: str | None = None) -> None:
        self.content = content
        self.refusal = refusal


class FakeChoice:
    def __init__(self, message: FakeMessage, finish_reason: str = "stop") -> None:
        self.message = message
        self.finish_reason = finish_reason


class FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeResponse:
    def __init__(
        self,
        content: str | None = None,
        *,
        refusal: str | None = None,
        prompt_tokens: int = 1200,
        completion_tokens: int = 340,
    ) -> None:
        self.choices = [FakeChoice(FakeMessage(content, refusal))]
        self.usage = FakeUsage(prompt_tokens, completion_tokens)


class FakeCompletions:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.completions = FakeCompletions(response, error)
        self.chat = type("Chat", (), {"completions": self.completions})()


@pytest.fixture
def install_client(monkeypatch: pytest.MonkeyPatch):
    """`openai.OpenAI` 를 대체하고, 생성 인자와 호출 인자를 기록한다."""
    created: dict[str, Any] = {}

    def _install(response: Any = None, error: Exception | None = None) -> FakeClient:
        client = FakeClient(response, error)

        def _factory(**kwargs: Any) -> FakeClient:
            created["kwargs"] = kwargs
            return client

        monkeypatch.setattr("app.llm_providers.openai_provider.OpenAI", _factory)
        client.created = created  # type: ignore[attr-defined]
        return client

    return _install


def _provider(**overrides: Any) -> OpenAIProvider:
    kwargs: dict[str, Any] = {"model": "gpt-test", "api_key": "sk-plain", "timeout_seconds": 5.0}
    kwargs.update(overrides)
    return OpenAIProvider(**kwargs)


# ------------------------------------------------------------------ analyze


def test_analyze_sends_strict_structured_output_schema(install_client) -> None:
    client = install_client(FakeResponse(json.dumps(RAW_RESULT)))
    provider = _provider()

    provider.analyze(LLMPrompt(user="마스킹된 대표 로그"))

    request = client.completions.calls[0]
    response_format = request["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True

    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    # strict 모드는 모든 property 가 required 여야 한다.
    assert set(schema["required"]) == set(schema["properties"])
    # strict 모드가 받지 않는 값 제약은 제거된다 (검증은 어댑터 밖에서 한다).
    assert "minItems" not in schema["properties"]["hypotheses"]
    assert schema["$defs"]["Hypothesis"]["additionalProperties"] is False


def test_analyze_returns_raw_dict_without_validation(install_client) -> None:
    install_client(FakeResponse(json.dumps(RAW_RESULT)))

    raw, input_tokens, output_tokens = _provider().analyze(LLMPrompt(user="로그"))

    assert raw == RAW_RESULT  # 스키마에 없는 키까지 그대로 — 검증하지 않는다는 증거
    assert (input_tokens, output_tokens) == (1200, 340)


def test_analyze_sends_system_and_omits_temperature_by_default(install_client) -> None:
    client = install_client(FakeResponse(json.dumps(RAW_RESULT)))

    _provider().analyze(LLMPrompt(system="너는 SRE 다", user="로그", max_output_tokens=999))

    request = client.completions.calls[0]
    assert request["messages"][0] == {"role": "system", "content": "너는 SRE 다"}
    assert request["messages"][-1]["content"] == "로그"
    assert request["max_completion_tokens"] == 999
    assert "temperature" not in request


def test_analyze_sends_temperature_when_given(install_client) -> None:
    client = install_client(FakeResponse(json.dumps(RAW_RESULT)))

    _provider().analyze(LLMPrompt(user="로그", temperature=0.2))

    assert client.completions.calls[0]["temperature"] == 0.2


def test_analyze_uses_base_url_and_decrypted_key(install_client) -> None:
    client = install_client(FakeResponse(json.dumps(RAW_RESULT)))
    provider = _provider(base_url="http://vllm.internal:8000/v1")

    provider.analyze(LLMPrompt(user="로그"))

    created = client.created["kwargs"]
    assert created["base_url"] == "http://vllm.internal:8000/v1"
    assert created["api_key"] == "sk-plain"
    assert created["timeout"] == 5.0


def test_analyze_allows_keyless_compatible_endpoint(install_client) -> None:
    client = install_client(FakeResponse(json.dumps(RAW_RESULT)))
    provider = _provider(api_key=None, base_url="http://vllm.internal:8000/v1")

    provider.analyze(LLMPrompt(user="로그"))

    assert client.created["kwargs"]["api_key"] == "not-required"


# -------------------------------------------------------------------- 오류


def test_sdk_error_is_wrapped_in_llm_error(install_client) -> None:
    install_client(error=FakeOpenAIError("rate limited", status_code=429))

    with pytest.raises(LLMError) as excinfo:
        _provider().analyze(LLMPrompt(user="로그"))

    assert excinfo.value.status_code == 429
    assert "429" in str(excinfo.value)


def test_broken_json_is_wrapped_in_llm_error(install_client) -> None:
    install_client(FakeResponse("{ not json"))

    with pytest.raises(LLMError):
        _provider().analyze(LLMPrompt(user="로그"))


def test_non_object_json_is_wrapped_in_llm_error(install_client) -> None:
    install_client(FakeResponse("[1, 2, 3]"))

    with pytest.raises(LLMError):
        _provider().analyze(LLMPrompt(user="로그"))


def test_refusal_is_wrapped_in_llm_error(install_client) -> None:
    install_client(FakeResponse(None, refusal="거부"))

    with pytest.raises(LLMError):
        _provider().analyze(LLMPrompt(user="로그"))


def test_empty_content_is_wrapped_in_llm_error(install_client) -> None:
    install_client(FakeResponse(None))

    with pytest.raises(LLMError):
        _provider().analyze(LLMPrompt(user="로그"))


# ----------------------------------------------------------- test_connection


def test_test_connection_uses_minimum_tokens(install_client) -> None:
    client = install_client(FakeResponse("ok", prompt_tokens=3, completion_tokens=1))

    result = _provider().test_connection()

    assert result.ok is True
    assert result.latency_ms is not None
    assert result.details["model"] == "gpt-test"
    request = client.completions.calls[0]
    assert request["max_completion_tokens"] == 1
    assert "response_format" not in request


def test_test_connection_reports_failure_without_raising(install_client) -> None:
    install_client(error=FakeOpenAIError("bad key", status_code=401))

    result = _provider().test_connection()

    assert result.ok is False
    assert "401" in result.message


def test_supports_structured_output_flag_is_true() -> None:
    assert OpenAIProvider.supports_structured_output is True
    assert OpenAIProvider.provider_name == "openai"


def test_model_is_required() -> None:
    with pytest.raises(ValueError):
        OpenAIProvider(model="")
