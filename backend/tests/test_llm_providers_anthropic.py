"""Anthropic 어댑터 테스트.

실제 API 는 절대 호출하지 않는다 — `anthropic.Anthropic` 클라이언트를 통째로 대체한다.
핵심은 구조화 출력 경로가 **두 단계**라는 것이다: 최신 `output_config.format` 을 먼저
시도하고, 모델·엔드포인트가 받지 않으면(400 또는 SDK 미지원) tool-use 강제로 폴백한다.
어느 경로든 반환값은 **원시 dict** 이고, 실패는 전부 `LLMError` 다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from anthropic import AnthropicError

from app.llm_providers.anthropic_provider import TOOL_NAME, AnthropicProvider
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


class FakeAnthropicError(AnthropicError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeBlock:
    def __init__(self, **fields: Any) -> None:
        for key, value in fields.items():
            setattr(self, key, value)


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(
        self,
        content: list[FakeBlock],
        *,
        input_tokens: int = 1200,
        output_tokens: int = 340,
    ) -> None:
        self.content = content
        self.usage = FakeUsage(input_tokens, output_tokens)


def text_response(payload: str) -> FakeResponse:
    return FakeResponse([FakeBlock(type="text", text=payload)])


def tool_use_response(payload: dict[str, Any]) -> FakeResponse:
    return FakeResponse(
        [
            FakeBlock(type="text", text="도구를 호출합니다"),
            FakeBlock(type="tool_use", name=TOOL_NAME, input=payload),
        ]
    )


class FakeMessages:
    """`messages.create` 호출을 기록하고, 호출 순서대로 결과를 돌려준다."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "unsupported-kwarg":
            # 구버전 SDK: output_config 를 모른다.
            raise TypeError("unexpected keyword argument 'output_config'")
        return outcome


class FakeClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.messages = FakeMessages(outcomes)


@pytest.fixture
def install_client(monkeypatch: pytest.MonkeyPatch):
    created: dict[str, Any] = {}

    def _install(*outcomes: Any) -> FakeClient:
        client = FakeClient(list(outcomes))

        def _factory(**kwargs: Any) -> FakeClient:
            created["kwargs"] = kwargs
            return client

        monkeypatch.setattr("app.llm_providers.anthropic_provider.Anthropic", _factory)
        client.created = created  # type: ignore[attr-defined]
        return client

    return _install


def _provider(**overrides: Any) -> AnthropicProvider:
    kwargs: dict[str, Any] = {
        "model": "claude-test",
        "api_key": "sk-ant-plain",
        "timeout_seconds": 5.0,
    }
    kwargs.update(overrides)
    return AnthropicProvider(**kwargs)


# ------------------------------------------------- output_config.format 경로


def test_analyze_sends_output_config_json_schema(install_client) -> None:
    client = install_client(text_response(json.dumps(RAW_RESULT)))
    provider = _provider()

    raw, input_tokens, output_tokens = provider.analyze(LLMPrompt(user="마스킹된 대표 로그"))

    assert raw == RAW_RESULT  # 원시 dict 그대로
    assert (input_tokens, output_tokens) == (1200, 340)
    assert provider.last_structured_mode == "output_config"

    request = client.messages.calls[0]
    schema = request["output_config"]["format"]["schema"]
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "minItems" not in schema["properties"]["limitations"]
    # max_tokens 는 Anthropic 필수 인자다.
    assert request["max_tokens"] > 0
    assert "tools" not in request


def test_analyze_sends_system_and_omits_temperature_by_default(install_client) -> None:
    client = install_client(text_response(json.dumps(RAW_RESULT)))

    _provider().analyze(LLMPrompt(system="너는 SRE 다", user="로그", max_output_tokens=777))

    request = client.messages.calls[0]
    assert request["system"] == "너는 SRE 다"
    assert request["messages"] == [{"role": "user", "content": "로그"}]
    assert request["max_tokens"] == 777
    assert "temperature" not in request


def test_analyze_uses_base_url_and_decrypted_key(install_client) -> None:
    client = install_client(text_response(json.dumps(RAW_RESULT)))

    _provider(base_url="http://gateway.internal").analyze(LLMPrompt(user="로그"))

    created = client.created["kwargs"]
    assert created["base_url"] == "http://gateway.internal"
    assert created["api_key"] == "sk-ant-plain"
    assert created["timeout"] == 5.0


# ------------------------------------------------------------- 폴백 (tool-use)


def test_falls_back_to_tool_use_on_400(install_client) -> None:
    client = install_client(
        FakeAnthropicError("output_config not supported", status_code=400),
        tool_use_response(RAW_RESULT),
    )
    provider = _provider()

    raw, input_tokens, output_tokens = provider.analyze(LLMPrompt(user="로그"))

    assert raw == RAW_RESULT
    assert (input_tokens, output_tokens) == (1200, 340)
    assert provider.last_structured_mode == "tool_use"

    fallback = client.messages.calls[1]
    assert fallback["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert fallback["tools"][0]["name"] == TOOL_NAME
    schema = fallback["tools"][0]["input_schema"]
    assert schema["additionalProperties"] is False
    assert "output_config" not in fallback


def test_falls_back_when_sdk_does_not_know_output_config(install_client) -> None:
    client = install_client("unsupported-kwarg", tool_use_response(RAW_RESULT))

    raw, _, _ = _provider().analyze(LLMPrompt(user="로그"))

    assert raw == RAW_RESULT
    assert len(client.messages.calls) == 2


def test_unrelated_type_error_is_not_retried(install_client) -> None:
    """임의의 TypeError 까지 폴백으로 삼키면 같은 프롬프트로 두 번 호출해 **이중 과금**이 난다.

    폴백은 "SDK 가 output_config 를 모른다"일 때만이다.
    """
    client = install_client(TypeError("unhashable type: 'dict'"), tool_use_response(RAW_RESULT))

    with pytest.raises(TypeError):
        _provider().analyze(LLMPrompt(user="로그"))

    assert len(client.messages.calls) == 1


def test_fallback_failure_is_wrapped_in_llm_error(install_client) -> None:
    install_client(
        FakeAnthropicError("bad request", status_code=400),
        FakeAnthropicError("still bad", status_code=400),
    )

    with pytest.raises(LLMError) as excinfo:
        _provider().analyze(LLMPrompt(user="로그"))

    assert excinfo.value.status_code == 400


# -------------------------------------------------------------------- 오류


def test_non_400_sdk_error_does_not_retry_and_is_wrapped(install_client) -> None:
    client = install_client(FakeAnthropicError("rate limited", status_code=429))

    with pytest.raises(LLMError) as excinfo:
        _provider().analyze(LLMPrompt(user="로그"))

    assert excinfo.value.status_code == 429
    # 과금 호출을 두 번 하지 않는다.
    assert len(client.messages.calls) == 1


def test_broken_json_is_wrapped_in_llm_error(install_client) -> None:
    install_client(text_response("{ not json"))

    with pytest.raises(LLMError):
        _provider().analyze(LLMPrompt(user="로그"))


def test_missing_text_block_is_wrapped_in_llm_error(install_client) -> None:
    install_client(FakeResponse([]))

    with pytest.raises(LLMError):
        _provider().analyze(LLMPrompt(user="로그"))


def test_missing_tool_use_block_is_wrapped_in_llm_error(install_client) -> None:
    install_client(
        FakeAnthropicError("nope", status_code=400),
        FakeResponse([FakeBlock(type="text", text="죄송합니다")]),
    )

    with pytest.raises(LLMError):
        _provider().analyze(LLMPrompt(user="로그"))


# ----------------------------------------------------------- test_connection


def test_test_connection_uses_minimum_tokens(install_client) -> None:
    client = install_client(
        FakeResponse([FakeBlock(type="text", text="ok")], input_tokens=3, output_tokens=1)
    )

    result = _provider().test_connection()

    assert result.ok is True
    assert result.latency_ms is not None
    assert result.details["model"] == "claude-test"
    request = client.messages.calls[0]
    assert request["max_tokens"] == 1
    assert "output_config" not in request


def test_test_connection_reports_failure_without_raising(install_client) -> None:
    install_client(FakeAnthropicError("bad key", status_code=401))

    result = _provider().test_connection()

    assert result.ok is False
    assert "401" in result.message


def test_supports_structured_output_flag_is_true() -> None:
    assert AnthropicProvider.supports_structured_output is True
    assert AnthropicProvider.provider_name == "anthropic"


def test_model_is_required() -> None:
    with pytest.raises(ValueError):
        AnthropicProvider(model="")
