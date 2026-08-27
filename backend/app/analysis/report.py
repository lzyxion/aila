"""Markdown 보고서 렌더링.

보고서는 **저장하지 않고 요청 시점에 렌더링**한다. 원천(`analysis_results` + 오류 그룹
메타데이터)이 이미 DB 에 있어 저장하면 같은 내용이 두 벌이 되고, 템플릿을 고치면 과거
분석도 새 형식으로 뽑을 수 있어야 하기 때문이다.

두 가지를 반드시 담는다.

1. **"LLM 이 생성한 원인 가설"** 표기 — 화면 밖으로 나가도 사실 확정으로 읽히지 않게 한다.
2. **원본 로그로 돌아갈 길** — 원문은 DB 에 없으므로, 그룹에 남은 라벨·시각과 정책 쿼리로
   로그 소스에서 다시 읽는 방법을 적는다. 셀렉터는 현재 유일한 어댑터인 Loki 문법(LogQL)
   으로 찍는다 — 코드 펜스의 ``logql`` 태그가 그 뜻이다.

들어가는 로그는 화면과 같은 **마스킹된 값**뿐이라 새 유출면이 생기지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis import integrations
from app.models import (
    AnalysisJob,
    AnalysisPolicy,
    AnalysisResult,
    AnalysisUsageRecord,
    ErrorGroup,
    ErrorSample,
    QueryRun,
)
from app.schemas.analysis import AnalysisResultSchema

#: 보고서 첫 줄의 고정 문구. 화면 밖으로 나가는 문서라 여기서 성격을 못 박는다.
DISCLAIMER = (
    "> **이 보고서의 원인·대응 내용은 LLM 이 생성한 원인 가설입니다.** 사실 확정이 아니며, "
    "아래 '원본 로그로 돌아가기' 의 조회 조건으로 반드시 원본을 확인하십시오.\n"
    "> 로그 본문은 모두 마스킹된 값이고, `<MASKED:종류>` 는 지워진 민감정보 자리입니다."
)

#: 보고서에 싣는 대표 로그 수 상한 (프롬프트와 같은 기준).
MAX_REPORT_SAMPLES = 3


def _iso(value: datetime | None) -> str:
    """ISO 8601. naive 는 UTC 로 간주한다 (SQLite 는 tz 를 버리고 돌려준다)."""
    if value is None:
        return "알 수 없음"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _escape_label_value(value: str) -> str:
    """LogQL selector 안에 넣을 수 있게 백슬래시·따옴표를 이스케이프한다."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def selector_from_labels(labels: dict[str, str]) -> str:
    """그룹 라벨로 원본 재조회용 selector 를 만든다.

    보고서의 "원본 로그로 돌아가기" 와 그룹 상세의 발생 추이(metric 쿼리)가 **같은
    selector** 를 써야 한다 — 화면이 보여주는 추이와 사람이 손으로 재조회한 결과가
    갈라지면 어느 쪽이 맞는지 알 수 없다.

    라벨이 하나도 없으면 `"{}"` 를 돌려준다. 이는 LogQL 로는 실행할 수 없는 값이므로,
    쿼리를 만드는 쪽은 이 반환값을 확인하고 건너뛰어야 한다.
    """
    pairs = [
        f'{key}="{_escape_label_value(str(value))}"'
        for key, value in sorted(labels.items())
        if value not in (None, "")
    ]
    if not pairs:
        return "{}"
    return "{" + ", ".join(pairs) + "}"


#: 모듈 내부에서 쓰던 이름 (호환).
_selector = selector_from_labels


def _cost_line(usage: AnalysisUsageRecord | None) -> str:
    if usage is None:
        return "- 토큰·비용: 기록 없음"
    currency = ""
    if isinstance(usage.pricing_snapshot, dict):
        currency = f" {usage.pricing_snapshot.get('currency', '')}".rstrip()
    cost: Decimal | None = usage.estimated_cost
    cost_text = f"{cost}{currency} (추정)" if cost is not None else "단가표에 모델이 없어 계산하지 않음"
    latency = f"{usage.latency_ms} ms" if usage.latency_ms is not None else "알 수 없음"
    return (
        f"- 토큰: 입력 {usage.input_tokens} / 출력 {usage.output_tokens}\n"
        f"- 추정 비용: {cost_text} — **추정값이며 정산 근거가 아닙니다.**\n"
        f"- 지연 시간: {latency}"
    )


def _bullets(items: list[str], empty: str) -> list[str]:
    masked = [integrations.mask(item) for item in items if item]
    if not masked:
        return [f"- {empty}"]
    return [f"- {item}" for item in masked]


def render_markdown(
    *,
    job: AnalysisJob,
    result: AnalysisResultSchema,
    group: ErrorGroup | None,
    samples: list[ErrorSample],
    policy: AnalysisPolicy | None,
    run: QueryRun | None,
    usage: AnalysisUsageRecord | None,
) -> str:
    service = (group.service if group is not None else None) or "알 수 없음"
    error_type = (group.error_type if group is not None else None) or "알 수 없음"
    labels: dict[str, Any] = dict(group.labels or {}) if group is not None else {}

    lines: list[str] = [
        f"# 오류 분석 보고서 — {service} / {error_type}",
        "",
        DISCLAIMER,
        "",
        "## 개요",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| 서비스 | {service} |",
        f"| 환경 | {(group.environment if group is not None else None) or '알 수 없음'} |",
        f"| 예외 타입 | {error_type} |",
        f"| 발생 횟수 | {group.count if group is not None else '알 수 없음'} |",
        f"| 발생 시각 범위 | {_iso(group.first_seen) if group else '알 수 없음'}"
        f" ~ {_iso(group.last_seen) if group else '알 수 없음'} |",
        f"| fingerprint | `{job.fingerprint}` |",
        f"| LLM 추정 심각도 | {result.severity.value} (발생량 기반 지표가 아닙니다) |",
        f"| 분석 모델 | {job.provider} / {job.model} |",
        f"| 프롬프트 버전 | {job.prompt_version} |",
        f"| 분석 시각 | {_iso(job.completed_at or job.requested_at)} |",
        "",
        "## 요약",
        "",
        integrations.mask(result.summary),
        "",
        "## LLM 이 생성한 원인 가설",
        "",
    ]

    for index, hypothesis in enumerate(result.hypotheses, start=1):
        lines.append(
            f"{index}. **{integrations.mask(hypothesis.cause)}** "
            f"(confidence {hypothesis.confidence:g} — 정렬용 힌트이며 확률이 아닙니다)"
        )
        for evidence in hypothesis.evidence:
            lines.append(f"   - 근거: `{integrations.mask(evidence)}`")

    lines += [
        "",
        "## 확인 절차",
        "",
        *_bullets(list(result.investigation_steps), "제시된 절차가 없습니다."),
        "",
        "## 대응 초안",
        "",
        *_bullets(list(result.mitigation), "제시된 대응이 없습니다."),
        "",
        "## 한계",
        "",
        *_bullets(list(result.limitations), "명시된 한계가 없습니다."),
        "",
        "## 원본 로그로 돌아가기",
        "",
        "원본(마스킹 전) 로그는 저장하지 않습니다. 아래 조건으로 로그 소스에서 다시 조회하십시오.",
        "",
        f"- 정책: {policy.name if policy is not None else '알 수 없음'}",
        "- 정책 쿼리:",
        "",
        "```logql",
        (policy.query if policy is not None else "(정책을 찾을 수 없습니다)"),
        "```",
        "",
        "- 그룹 라벨 selector:",
        "",
        "```logql",
        _selector({key: str(value) for key, value in labels.items()}),
        "```",
        "",
        f"- 조회 기간(그룹 발생 범위): {_iso(group.first_seen) if group else '알 수 없음'}"
        f" ~ {_iso(group.last_seen) if group else '알 수 없음'}",
        f"- 조회 회차(query_run): {run.id if run is not None else '알 수 없음'}",
        "",
        "## 대표 로그 (마스킹됨)",
        "",
    ]

    if not samples:
        lines.append("- 저장된 대표 로그가 없습니다.")
    for index, sample in enumerate(samples[:MAX_REPORT_SAMPLES], start=1):
        lines += [
            f"{index}. `{_iso(sample.occurred_at)}`",
            "",
            "```text",
            integrations.mask(sample.masked_log),
            "```",
            "",
        ]

    lines += [
        "## 사용량",
        "",
        _cost_line(usage),
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_report(db: Session, job_id: int) -> str:
    """저장된 결과 + 그룹 메타데이터를 요청 시점에 Markdown 으로 조립한다."""
    job = db.get(AnalysisJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"분석 작업 {job_id} 을(를) 찾을 수 없습니다.",
        )
    stored = db.scalar(select(AnalysisResult).where(AnalysisResult.analysis_job_id == job.id))
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"분석 작업 {job_id} 에 결과가 없어 보고서를 만들 수 없습니다 "
                f"(현재 상태: {job.status})."
            ),
        )

    group = db.get(ErrorGroup, job.error_group_id)
    run = db.get(QueryRun, group.query_run_id) if group is not None else None
    policy = db.get(AnalysisPolicy, run.policy_id) if run is not None else None
    samples = (
        list(
            db.scalars(
                select(ErrorSample)
                .where(ErrorSample.error_group_id == group.id)
                .order_by(ErrorSample.occurred_at.asc(), ErrorSample.id.asc())
                .limit(MAX_REPORT_SAMPLES)
            ).all()
        )
        if group is not None
        else []
    )
    usage = db.scalar(
        select(AnalysisUsageRecord).where(AnalysisUsageRecord.analysis_job_id == job.id)
    )

    return render_markdown(
        job=job,
        result=AnalysisResultSchema.model_validate(stored.result_json),
        group=group,
        samples=samples,
        policy=policy,
        run=run,
        usage=usage,
    )


__all__ = [
    "DISCLAIMER",
    "MAX_REPORT_SAMPLES",
    "render_markdown",
    "render_report",
    "selector_from_labels",
]
