"""LLM 구조화 분석 결과 스키마 (설계 문서 "LLM 분석 설계").

`hypotheses` 와 `limitations` 가 **필수**인 것이 안전장치다. 원인을 하나로 단정할 수
없게 구조가 강제하고, 모델이 로그만으로 알 수 없는 것을 스스로 적게 만든다.
`confidence` 는 정렬용 힌트일 뿐 확률로 읽지 않는다.

검증 위치 계약: 이 스키마 검증은 **`LLMProvider` 어댑터 밖 공통 경로에서 한 번만**
수행한다. 어댑터는 원시 JSON dict 만 돌려준다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.enums import Severity


class Hypothesis(BaseModel):
    """원인 가설 하나."""

    model_config = ConfigDict(extra="forbid")

    cause: str = Field(min_length=1, description="추정 원인")
    confidence: float = Field(ge=0.0, le=1.0, description="정렬용 힌트. 확률이 아니다.")
    evidence: list[str] = Field(
        default_factory=list, description="로그에서 근거가 된 조각 (마스킹된 값)"
    )


class AnalysisResultSchema(BaseModel):
    """LLM 이 반환해야 하는 구조. `analysis_results.result_json` 에 그대로 저장된다."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, description="한국어 한 줄 요약")
    severity: Severity = Field(description="LLM 추정 심각도. 발생량 기반 지표와 다르다.")
    hypotheses: list[Hypothesis] = Field(
        min_length=1, description="필수. 최소 1 개 — 단정 금지 장치."
    )
    investigation_steps: list[str] = Field(default_factory=list, description="확인 순서")
    mitigation: list[str] = Field(default_factory=list, description="대응·완화 초안")
    limitations: list[str] = Field(
        min_length=1, description="필수. 로그만으로 알 수 없는 것을 명시."
    )


def analysis_json_schema() -> dict[str, Any]:
    """프로바이더 structured outputs 에 넘길 JSON Schema.

    프로바이더별 구조화 출력 방식(OpenAI structured outputs, Anthropic output_config)은
    어댑터 안에 가두되, **스키마 자체는 하나**여야 결과가 프로바이더마다 다르게 깨지지 않는다.
    """
    return AnalysisResultSchema.model_json_schema()


def parse_analysis_result(raw: dict[str, Any]) -> AnalysisResultSchema:
    """어댑터가 돌려준 원시 JSON 을 검증한다. 공통 경로 전용 진입점."""
    return AnalysisResultSchema.model_validate(raw)


__all__ = [
    "AnalysisResultSchema",
    "Hypothesis",
    "analysis_json_schema",
    "parse_analysis_result",
]
