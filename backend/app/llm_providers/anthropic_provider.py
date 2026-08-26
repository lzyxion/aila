"""Anthropic 어댑터 (`anthropic` SDK).

구조화 출력은 최신 방식인 **`output_config.format`**(json_schema)로 강제한다.
엔드포인트나 모델이 이를 받지 않으면(400 / SDK 가 인자를 모름) **tool-use 강제**
(`tool_choice={"type": "tool", ...}`) 로 한 번 폴백한다 — 둘 다 모델이 스키마에 맞는
JSON 을 내도록 서버가 강제하는 방식이라 프롬프트 유도로 내려가지 않는다.

>>> 검증 위치 계약 <<<
`analyze()` 는 **원시 dict** 만 돌려준다. Pydantic 검증은 어댑터 밖에서 한 번만 한다.
SDK 예외·JSON 파싱 실패는 전부 `LLMError` 로 감싼다.
"""

from __future__ import annotations

import json
import time
from typing import Any

from anthropic import Anthropic, AnthropicError

from app.config import get_settings
from app.llm_providers.schema import to_strict_json_schema
from app.providers.llm import LLMAnalyzeResult, LLMError, LLMPrompt, LLMProvider
from app.schemas.logrecord import ConnectionTestResult

#: 폴백 경로에서 쓰는 도구 이름.
TOOL_NAME = "record_analysis"
TOOL_DESCRIPTION = "로그 분석 결과를 정해진 스키마로 기록한다."

#: Anthropic 은 max_tokens 가 필수다.
DEFAULT_MAX_OUTPUT_TOKENS = 2048

#: 연결 테스트는 실제 과금 호출이므로 최소 토큰만 쓴다.
TEST_MAX_OUTPUT_TOKENS = 1
TEST_PROMPT = "ping"

#: 모델 목록 페이지 크기 (Anthropic 상한). 기본값은 작아서 목록이 잘린다.
MODEL_LIST_LIMIT = 1000


class AnthropicProvider(LLMProvider):
    """Anthropic Messages API 어댑터."""

    provider_name = "anthropic"
    #: output_config.format(없으면 tool-use 강제) 둘 다 네이티브 구조화 출력이다.
    supports_structured_output = True

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        if not model:
            raise ValueError("model 이 필요합니다.")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url or None
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else get_settings().llm_timeout_seconds
        )
        self._client: Any | None = None
        #: 마지막 analyze() 가 어느 경로로 결과를 얻었는지 ("output_config" | "tool_use").
        self.last_structured_mode: str | None = None

    # ------------------------------------------------------------------ 클라이언트

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self) -> Any:
        return _build_client(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout_seconds=self.timeout_seconds,
        )

    # ------------------------------------------------------------------ 모델 목록

    @classmethod
    def list_models(
        cls,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> list[str]:
        """`client.models.list()` 로 모델 id 목록을 가져온다 (무과금 호출).

        **classmethod 인 이유**: 모델 목록은 model 을 고르기 *전에* 필요하므로
        `model` 이 필수인 생성자를 태울 수 없다. `LLMProvider` ABC 의 연산은
        `test_connection` / `analyze` 둘뿐이라는 어댑터 계약도 그대로 둔다.

        기본 페이지 크기가 작아 최신 모델만 보이는 일이 없도록 `limit` 을 명시한다.
        순서는 프로바이더가 준 그대로 둔다 (Anthropic 은 최신순).
        """
        client = _build_client(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=(
                timeout_seconds
                if timeout_seconds is not None
                else get_settings().llm_timeout_seconds
            ),
        )
        try:
            page = client.models.list(limit=MODEL_LIST_LIMIT)
        except TypeError:
            # `limit` 을 모르는 SDK 버전 — 기본 페이지로 폴백한다 (무과금이라 안전).
            page = client.models.list()
        except AnthropicError as exc:
            raise LLMError(
                f"Anthropic 모델 목록 조회에 실패했습니다: {_error_message(exc)}",
                status_code=_status_code(exc),
            ) from exc
        return _model_ids(page)

    # ---------------------------------------------------------------------- 연산

    def test_connection(self) -> ConnectionTestResult:
        """최소 토큰 호출로 키·모델을 확인한다. 예외를 던지지 않는다."""
        started = time.perf_counter()
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=TEST_MAX_OUTPUT_TOKENS,
                messages=[{"role": "user", "content": TEST_PROMPT}],
            )
        except LLMError as exc:
            return ConnectionTestResult(ok=False, message=str(exc))
        except AnthropicError as exc:
            return ConnectionTestResult(ok=False, message=_error_message(exc))

        latency_ms = int((time.perf_counter() - started) * 1000)
        input_tokens, output_tokens = _usage_tokens(response)
        return ConnectionTestResult(
            ok=True,
            message="LLM 연결에 성공했습니다.",
            latency_ms=latency_ms,
            details={
                "provider": self.provider_name,
                "model": self.model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )

    def analyze(self, prompt: LLMPrompt) -> LLMAnalyzeResult:
        schema = to_strict_json_schema(prompt.json_schema)
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": prompt.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
            "messages": [{"role": "user", "content": prompt.user}],
        }
        if prompt.system:
            request["system"] = prompt.system
        if prompt.temperature is not None:
            # 최신 모델은 temperature 를 거부하므로 명시된 경우에만 싣는다.
            request["temperature"] = prompt.temperature

        client = self.client
        try:
            response = client.messages.create(
                **request,
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except TypeError as exc:
            # SDK 가 output_config 를 모르는 버전일 때만 폴백한다.
            # 임의의 TypeError 까지 삼키면 (우리 쪽 인자 조립 버그, SDK 내부 오류)
            # 같은 프롬프트로 두 번 호출해 **이중 과금**이 난다.
            if "output_config" not in str(exc):
                raise
            response = None
        except AnthropicError as exc:
            if not _is_unsupported_request(exc):
                raise LLMError(
                    f"Anthropic 호출에 실패했습니다: {_error_message(exc)}",
                    status_code=_status_code(exc),
                ) from exc
            # 모델/엔드포인트가 구조화 출력을 받지 않는다 -> 폴백.
            response = None

        if response is not None:
            self.last_structured_mode = "output_config"
            raw = _extract_json_from_text(response)
            input_tokens, output_tokens = _usage_tokens(response)
            return LLMAnalyzeResult(
                raw=raw, input_tokens=input_tokens, output_tokens=output_tokens
            )

        try:
            response = client.messages.create(
                **request,
                tools=[
                    {
                        "name": TOOL_NAME,
                        "description": TOOL_DESCRIPTION,
                        "input_schema": schema,
                    }
                ],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
        except AnthropicError as exc:
            raise LLMError(
                f"Anthropic 호출에 실패했습니다: {_error_message(exc)}",
                status_code=_status_code(exc),
            ) from exc

        self.last_structured_mode = "tool_use"
        raw = _extract_json_from_tool_use(response)
        input_tokens, output_tokens = _usage_tokens(response)
        return LLMAnalyzeResult(raw=raw, input_tokens=input_tokens, output_tokens=output_tokens)


# ------------------------------------------------------------------- 내부 헬퍼


def _build_client(
    *, api_key: str | None, base_url: str | None, timeout_seconds: float
) -> Any:
    """SDK 클라이언트 생성. 인스턴스 경로와 모델 목록 경로가 같은 규칙을 쓴다."""
    kwargs: dict[str, Any] = {"timeout": timeout_seconds}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    try:
        return Anthropic(**kwargs)
    except AnthropicError as exc:  # 키 누락 등
        raise LLMError(f"Anthropic 클라이언트를 만들 수 없습니다: {exc}") from exc


def _model_ids(page: Any) -> list[str]:
    """`models.list()` 응답에서 id 문자열만 뽑는다 (순서 유지, 중복 제거)."""
    items = getattr(page, "data", None)
    if items is None:
        try:
            items = list(page)
        except TypeError as exc:  # pragma: no cover - SDK 형태가 바뀐 경우 방어
            raise LLMError("모델 목록 응답 형식을 해석할 수 없습니다.") from exc

    seen: set[str] = set()
    ids: list[str] = []
    for item in items or []:
        model_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        if isinstance(model_id, str) and model_id and model_id not in seen:
            seen.add(model_id)
            ids.append(model_id)
    return ids


def _blocks(response: Any) -> list[Any]:
    content = getattr(response, "content", None) or []
    return list(content)


def _extract_json_from_text(response: Any) -> dict[str, Any]:
    """`output_config.format` 응답 — 첫 text 블록이 스키마에 맞는 JSON 이다."""
    for block in _blocks(response):
        if getattr(block, "type", None) != "text":
            continue
        text = getattr(block, "text", None)
        if not text:
            continue
        return _loads_object(text)
    raise LLMError("Anthropic 응답에 구조화 출력 text 블록이 없습니다.")


def _extract_json_from_tool_use(response: Any) -> dict[str, Any]:
    """폴백 응답 — 강제된 tool_use 블록의 input 이 곧 결과 dict 다."""
    for block in _blocks(response):
        if getattr(block, "type", None) != "tool_use":
            continue
        if getattr(block, "name", None) != TOOL_NAME:
            continue
        raw = getattr(block, "input", None)
        if isinstance(raw, str):
            return _loads_object(raw)
        if not isinstance(raw, dict):
            raise LLMError(f"tool_use.input 이 object 가 아닙니다: {type(raw).__name__}")
        return raw
    raise LLMError("Anthropic 응답에 tool_use 블록이 없습니다.")


def _loads_object(text: str) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise LLMError(f"구조화 출력 JSON 파싱에 실패했습니다: {exc}") from exc
    if not isinstance(raw, dict):
        raise LLMError(f"구조화 출력이 object 가 아닙니다: {type(raw).__name__}")
    return raw


def _usage_tokens(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return input_tokens, output_tokens


def _status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    return int(status) if isinstance(status, int) else None


def _is_unsupported_request(exc: Exception) -> bool:
    """400 이면 "이 모델/엔드포인트가 구조화 출력을 모른다" 로 보고 폴백한다."""
    return _status_code(exc) == 400


def _error_message(exc: Exception) -> str:
    status = _status_code(exc)
    text = str(exc) or exc.__class__.__name__
    return f"[{status}] {text}" if status is not None else text


__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "MODEL_LIST_LIMIT",
    "TOOL_NAME",
    "AnthropicProvider",
]
