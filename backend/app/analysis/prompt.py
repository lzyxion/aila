"""프롬프트 조립 (설계 문서 "LLM 분석 설계").

프롬프트에 들어가는 것은 **고정 목록**이다. 목록을 고정해야 토큰 상한이 예측 가능해지고,
`prompt_version` 을 결과와 함께 저장하는 것이 의미를 갖는다.

- 오류 그룹의 서비스명·환경·발생 시각 범위·발생 횟수
- 마스킹된 대표 로그 **최대 3 개**
- 정규화 메시지, 예외 타입, 상위 스택 프레임
- (있으면) 최근 오류 추이

여기에 없는 것은 넣지 않는다. 특히 원본(마스킹 전) 로그는 애초에 DB 에 없다.

>>> 이중 마스킹 <<<
`build_prompt()` 는 조립이 끝난 문자열 **전체**에 `mask()` 를 한 번 더 건다.
샘플은 저장 시점에 이미 마스킹돼 있지만, 경로가 다르면 한쪽만 새는 것을 아무도
잡아주지 않는다 — 정규화 메시지·라벨·스택 프레임처럼 다른 경로로 들어온 값까지
전송 직전에 한 번에 덮는 것이 이 두 번째 호출의 목적이다. `mask()` 는 멱등이라
이미 마스킹된 부분은 그대로 남는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.analysis import integrations
from app.config import get_settings
from app.providers.llm import LLMPrompt
from app.schemas.analysis import analysis_json_schema

#: 대표 로그 한 줄의 길이 상한 (설계: "분석 요청마다 로그 수와 길이를 제한한다").
MAX_SAMPLE_CHARS = 2000
#: 스택트레이스 길이 상한.
MAX_STACKTRACE_CHARS = 1200
#: 프롬프트에 싣는 추이 포인트 수 상한.
MAX_TREND_POINTS = 24

TRUNCATION_MARK = "…(생략)"

SYSTEM_PROMPT = (
    "당신은 애플리케이션 오류 로그를 읽는 한국어 SRE 보조자입니다.\n"
    "주어진 것은 마스킹된 대표 로그 몇 개와 그룹 메타데이터뿐입니다. "
    "민감정보는 <MASKED:종류> 로 치환되어 있으니 그 안의 값을 추측하려 하지 마십시오.\n"
    "원인을 하나로 단정하지 말고 **가설**로 제시하고, 각 가설에는 로그에서 찾은 근거를 붙이십시오.\n"
    "로그만으로 확인할 수 없는 것은 limitations 에 반드시 적으십시오.\n"
    "모든 서술은 한국어로 작성하고, 지정된 JSON 스키마에 맞는 객체 하나만 반환하십시오."
)


@dataclass(frozen=True)
class PromptSample:
    """프롬프트에 실리는 대표 로그 하나. `masked_log` 는 이미 마스킹된 값이다."""

    occurred_at: datetime | None
    masked_log: str
    stacktrace: str | None = None


@dataclass(frozen=True)
class PromptContext:
    """프롬프트 고정 목록에 대응하는 입력. ORM 에 직접 매이지 않게 분리한다."""

    service: str | None
    environment: str | None
    error_type: str | None
    normalized_message: str
    count: int
    first_seen: datetime | None
    last_seen: datetime | None
    labels: dict[str, str]
    top_stack_frame: str | None
    samples: Sequence[PromptSample] = ()
    #: (시각, 건수) — metric 쿼리 기반 최근 추이. 없으면 비운다.
    trend: Sequence[tuple[datetime, float]] = ()


def _iso(value: datetime | None) -> str:
    """ISO 8601. naive 는 UTC 로 간주한다 (SQLite 는 tz 를 버리고 돌려준다)."""
    if value is None:
        return "알 수 없음"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + TRUNCATION_MARK


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return "없음"
    return ", ".join(f"{key}={value}" for key, value in sorted(labels.items()) if value != "")


def context_from_group(
    group: Any, samples: Sequence[Any], *, trend: Sequence[tuple[datetime, float]] = ()
) -> PromptContext:
    """`ErrorGroup` + `ErrorSample` ORM → `PromptContext` (속성 접근만 한다)."""
    return PromptContext(
        service=group.service,
        environment=group.environment,
        error_type=group.error_type,
        normalized_message=group.normalized_message or "",
        count=group.count or 0,
        first_seen=group.first_seen,
        last_seen=group.last_seen,
        labels=dict(group.labels or {}),
        top_stack_frame=group.top_stack_frame,
        samples=[
            PromptSample(
                occurred_at=sample.occurred_at,
                masked_log=sample.masked_log,
                stacktrace=sample.stacktrace,
            )
            for sample in samples
        ],
        trend=trend,
    )


def render_user_message(context: PromptContext, *, max_samples: int | None = None) -> str:
    """고정 목록을 Markdown 으로 조립한다 (마스킹은 `build_prompt` 가 건다)."""
    limit = max_samples if max_samples is not None else get_settings().prompt_max_samples
    limit = max(0, limit)

    lines: list[str] = [
        "다음 오류 그룹의 원인 가설과 확인 절차를 분석해 주십시오.",
        "",
        "## 오류 그룹",
        f"- 서비스: {context.service or '알 수 없음'}",
        f"- 환경: {context.environment or '알 수 없음'}",
        f"- 발생 시각 범위: {_iso(context.first_seen)} ~ {_iso(context.last_seen)}",
        f"- 발생 횟수: {context.count}",
        f"- 예외 타입: {context.error_type or '알 수 없음'}",
        f"- 정규화 메시지: {_truncate(context.normalized_message, MAX_SAMPLE_CHARS)}",
        f"- 상위 스택 프레임: {context.top_stack_frame or '알 수 없음'}",
        f"- 라벨: {_format_labels(context.labels)}",
    ]

    samples = list(context.samples)[:limit]
    lines += ["", f"## 마스킹된 대표 로그 (최대 {limit} 개, 실제 {len(samples)} 개)"]
    if not samples:
        lines.append("- 대표 로그가 없습니다.")
    for index, sample in enumerate(samples, start=1):
        body = _truncate(sample.masked_log, MAX_SAMPLE_CHARS)
        lines.append(f"{index}. [{_iso(sample.occurred_at)}] {body}")
        if sample.stacktrace:
            stack = _truncate(sample.stacktrace, MAX_STACKTRACE_CHARS)
            lines += ["   ```", *(f"   {line}" for line in stack.splitlines()), "   ```"]

    trend = list(context.trend)[:MAX_TREND_POINTS]
    if trend:
        lines += ["", "## 최근 추이 (metric 쿼리 기준)"]
        lines += [f"- {_iso(point_at)}: {value:g}" for point_at, value in trend]

    lines += [
        "",
        "## 요청",
        "- 원인을 단정하지 말고 가설로 제시하고, 각 가설에 로그 근거를 붙여 주십시오.",
        "- 확인 절차와 대응 초안은 이 로그로 실제 실행 가능한 것만 적어 주십시오.",
        "- 로그만으로 알 수 없는 것은 limitations 에 남겨 주십시오.",
    ]
    return "\n".join(lines)


def build_prompt(
    context: PromptContext,
    *,
    max_samples: int | None = None,
    extra_mask_patterns: Sequence[str] = (),
    prompt_version: str | None = None,
) -> LLMPrompt:
    """전송 직전 프롬프트. 조립 결과 **전체**에 마스킹을 한 번 더 건다."""
    settings = get_settings()
    user = render_user_message(context, max_samples=max_samples)
    return LLMPrompt(
        system=integrations.mask(SYSTEM_PROMPT, extra_mask_patterns),
        user=integrations.mask(user, extra_mask_patterns),
        json_schema=analysis_json_schema(),
        prompt_version=prompt_version or settings.prompt_version,
    )


__all__ = [
    "MAX_SAMPLE_CHARS",
    "MAX_STACKTRACE_CHARS",
    "MAX_TREND_POINTS",
    "SYSTEM_PROMPT",
    "PromptContext",
    "PromptSample",
    "build_prompt",
    "context_from_group",
    "render_user_message",
]
