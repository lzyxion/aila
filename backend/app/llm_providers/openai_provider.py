"""OpenAI 어댑터 (`openai` SDK).

구조화 출력은 chat completions 의 **structured outputs**(`response_format` 의
`json_schema` + `strict: true`)로 강제한다 — 프롬프트로 "JSON 만 답하라" 고 부탁하는
방식은 응답이 깨졌을 때 처리 경로가 프로바이더마다 갈린다.

`base_url` 을 주면 OpenAI 호환 엔드포인트(Azure 게이트웨이, vLLM 등)로 그대로 보낸다.

>>> 검증 위치 계약 <<<
`analyze()` 는 **원시 dict** 만 돌려준다. Pydantic 검증은 어댑터 밖에서 한 번만 한다.
SDK 예외·JSON 파싱 실패는 전부 `LLMError` 로 감싼다.
"""

from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI, OpenAIError

from app.config import get_settings
from app.llm_providers.schema import to_strict_json_schema
from app.providers.llm import LLMAnalyzeResult, LLMError, LLMPrompt, LLMProvider
from app.schemas.logrecord import ConnectionTestResult

#: structured outputs 스키마 이름 (응답 내용에는 영향이 없다).
SCHEMA_NAME = "aila_analysis_result"

#: `LLMPrompt.max_output_tokens` 가 없을 때의 출력 상한.
DEFAULT_MAX_OUTPUT_TOKENS = 2048

#: 연결 테스트는 실제 과금 호출이므로 최소 토큰만 쓴다.
TEST_MAX_OUTPUT_TOKENS = 1
TEST_PROMPT = "ping"


class OpenAIProvider(LLMProvider):
    """OpenAI / OpenAI 호환 엔드포인트 어댑터."""

    provider_name = "openai"
    #: chat completions structured outputs 를 쓰므로 네이티브 지원이다.
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

    # ------------------------------------------------------------------ 클라이언트

    @property
    def client(self) -> Any:
        """SDK 클라이언트를 지연 생성한다 (생성만으로는 네트워크를 쓰지 않는다)."""
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self) -> Any:
        kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        elif self.base_url:
            # 인증이 없는 호환 엔드포인트도 SDK 는 키를 요구한다.
            kwargs["api_key"] = "not-required"
        if self.base_url:
            kwargs["base_url"] = self.base_url

        try:
            return OpenAI(**kwargs)
        except OpenAIError as exc:  # 키 누락 등
            raise LLMError(f"OpenAI 클라이언트를 만들 수 없습니다: {exc}") from exc

    # ---------------------------------------------------------------------- 연산

    def test_connection(self) -> ConnectionTestResult:
        """최소 토큰 호출로 키·모델·엔드포인트를 확인한다. 예외를 던지지 않는다."""
        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": TEST_PROMPT}],
                max_completion_tokens=TEST_MAX_OUTPUT_TOKENS,
            )
        except LLMError as exc:
            return ConnectionTestResult(ok=False, message=str(exc))
        except OpenAIError as exc:
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
        messages: list[dict[str, str]] = []
        if prompt.system:
            messages.append({"role": "system", "content": prompt.system})
        messages.append({"role": "user", "content": prompt.user})

        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": SCHEMA_NAME,
                    "strict": True,
                    "schema": to_strict_json_schema(prompt.json_schema),
                },
            },
            "max_completion_tokens": prompt.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS,
        }
        if prompt.temperature is not None:
            # 최신 모델은 temperature 를 거부하므로 명시된 경우에만 싣는다.
            request["temperature"] = prompt.temperature

        try:
            response = self.client.chat.completions.create(**request)
        except OpenAIError as exc:
            raise LLMError(
                f"OpenAI 호출에 실패했습니다: {_error_message(exc)}",
                status_code=_status_code(exc),
            ) from exc

        raw = _extract_json(response)
        input_tokens, output_tokens = _usage_tokens(response)
        return LLMAnalyzeResult(raw=raw, input_tokens=input_tokens, output_tokens=output_tokens)


# ------------------------------------------------------------------- 내부 헬퍼


def _extract_json(response: Any) -> dict[str, Any]:
    """structured outputs 응답에서 원시 dict 를 꺼낸다 (검증은 하지 않는다)."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise LLMError("OpenAI 응답에 choices 가 없습니다.")

    choice = choices[0]
    message = getattr(choice, "message", None)
    if message is None:
        raise LLMError("OpenAI 응답에 message 가 없습니다.")

    refusal = getattr(message, "refusal", None)
    if refusal:
        raise LLMError(f"모델이 응답을 거부했습니다: {refusal}")

    content = getattr(message, "content", None)
    if not content:
        finish_reason = getattr(choice, "finish_reason", None)
        raise LLMError(f"OpenAI 응답 본문이 비어 있습니다 (finish_reason={finish_reason}).")

    try:
        raw = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise LLMError(f"구조화 출력 JSON 파싱에 실패했습니다: {exc}") from exc

    if not isinstance(raw, dict):
        raise LLMError(f"구조화 출력이 object 가 아닙니다: {type(raw).__name__}")
    return raw


def _usage_tokens(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    return input_tokens, output_tokens


def _status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    return int(status) if isinstance(status, int) else None


def _error_message(exc: Exception) -> str:
    status = _status_code(exc)
    text = str(exc) or exc.__class__.__name__
    return f"[{status}] {text}" if status is not None else text


__all__ = ["DEFAULT_MAX_OUTPUT_TOKENS", "SCHEMA_NAME", "OpenAIProvider"]
