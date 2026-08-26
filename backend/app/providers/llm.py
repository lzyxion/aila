"""`LLMProvider` — LLM 프로바이더 추상 인터페이스.

연산은 두 개뿐이다: `test_connection()` 과 `analyze(prompt)`.
구조화 출력을 얻는 프로바이더별 방식(OpenAI structured outputs,
Anthropic structured outputs 등)은 **어댑터 안에 가둔다.**

>>> 검증 위치 계약 <<<
`analyze()` 는 **원시 JSON dict 를 그대로** 돌려준다. Pydantic 스키마 검증
(`app.schemas.analysis.parse_analysis_result`)은 **어댑터 밖 공통 경로에서 한 번만**
한다. 검증을 어댑터마다 두면 프로바이더별로 다르게 깨져서, 구조화 출력이 깨졌을 때의
처리 경로가 하나로 모이지 않는다. 어댑터 안에서 절대 검증하지 말 것.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis import analysis_json_schema
from app.schemas.logrecord import ConnectionTestResult


class LLMError(RuntimeError):
    """LLM 호출 실패의 공통 예외 타입. 어댑터는 SDK 고유 예외를 이것으로 감싼다."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMPrompt(BaseModel):
    """조립이 끝난 프롬프트.

    프롬프트에 들어가는 내용은 고정 목록이다(설계 문서 "LLM 분석 설계") — 서비스명·환경·
    발생 시각 범위·발생 횟수, **마스킹된 대표 로그 최대 3 개**, 정규화 메시지·예외 타입·
    상위 스택 프레임, 선택적으로 배포 버전과 최근 추이. 목록을 고정해야 토큰 상한이
    예측 가능해진다.

    `user` 에 들어가는 로그는 반드시 마스킹된 값이어야 한다 (LLM 전송 직전 마스킹).
    """

    model_config = ConfigDict(extra="forbid")

    system: str | None = None
    user: str = Field(min_length=1)
    #: 구조화 출력 스키마. 기본값은 공통 분석 결과 스키마.
    json_schema: dict[str, Any] = Field(default_factory=analysis_json_schema)
    max_output_tokens: int | None = None
    temperature: float | None = None
    #: 결과와 함께 저장되는 프롬프트 템플릿 버전.
    prompt_version: str = "v1"


class LLMAnalyzeResult(NamedTuple):
    """`analyze()` 반환값. 튜플로 언패킹할 수 있다.

    `(raw, input_tokens, output_tokens) = provider.analyze(prompt)`

    `raw` 는 **검증되지 않은** 원시 JSON dict 다.
    """

    raw: dict[str, Any]
    input_tokens: int
    output_tokens: int


class LLMProvider(ABC):
    """LLM 어댑터 인터페이스.

    구현체는 생성자에서 model / api_key(복호화된 평문) / base_url 을 받는다.
    """

    #: 프로바이더 식별자 (`llm_connections.provider` 값과 같아야 한다).
    provider_name: str = "openai"

    #: capability 플래그 — 네이티브 structured outputs 지원 여부.
    #: False 인 구현체는 프롬프트로 JSON 을 유도하고 파싱까지 어댑터 안에서 처리한다.
    supports_structured_output: bool = True

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """연결·키 테스트. 실제 과금 호출이므로 **최소 토큰**으로 보낸다."""

    @abstractmethod
    def analyze(self, prompt: LLMPrompt) -> LLMAnalyzeResult:
        """분석 1 회 호출.

        반환은 원시 JSON dict 와 토큰 수뿐이다. 검증·비용 계산·저장은 호출자 몫이다.
        실패 시 `LLMError` 를 던진다.
        """


__all__ = ["LLMAnalyzeResult", "LLMError", "LLMPrompt", "LLMProvider"]
