"""오류 그룹 목록·상세 서비스.

Phase 1 담당 트랙: **정책 API**

계약상 제약:
- `error_groups` 는 `query_run_id` 에 매달려 있어 **조회 1 회 안에서만 유효**하다.
  조회 회차를 넘는 추적은 `fingerprint` 값 기준 조인으로만 한다.
- 그래서 `analysis_status` 는 그룹 id 가 아니라 **fingerprint 기준**이다. 이게 없으면
  새 조회마다 그룹이 새 id 를 받아 어제 분석한 오류가 오늘 "미분석"으로 보이고,
  그대로 중복 과금이 된다.
- 응답에 실리는 로그는 전부 마스킹된 값이다 (원본은 저장하지 않는다).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AnalysisJob, AnalysisResult, ErrorGroup, ErrorSample, QueryRun
from app.schemas.api import (
    AnalysisJobSummary,
    ErrorGroupDetail,
    ErrorGroupListResponse,
    ErrorGroupSummary,
    ErrorSampleRead,
)

#: (job, result) 쌍. result 는 아직 완료되지 않은 작업이면 None 이다.
JobRow = tuple[AnalysisJob, AnalysisResult | None]


def _analysis_rows(db: Session, fingerprints: Sequence[str]) -> list[JobRow]:
    """fingerprint 로 분석 작업 + 결과를 읽는다 (오래된 것부터)."""
    if not fingerprints:
        return []
    stmt = (
        select(AnalysisJob, AnalysisResult)
        .outerjoin(AnalysisResult, AnalysisResult.analysis_job_id == AnalysisJob.id)
        .where(AnalysisJob.fingerprint.in_(list(fingerprints)))
        .order_by(AnalysisJob.requested_at.asc(), AnalysisJob.id.asc())
    )
    return [(job, result) for job, result in db.execute(stmt).all()]


def latest_analysis_by_fingerprint(
    db: Session, fingerprints: Iterable[str]
) -> dict[str, JobRow]:
    """fingerprint -> 가장 최근 분석 작업(+결과).

    조회 회차를 넘어 "이미 분석했는가"를 판정하는 유일한 경로다.
    """
    unique = list(dict.fromkeys(fingerprints))
    latest: dict[str, JobRow] = {}
    for job, result in _analysis_rows(db, unique):
        latest[job.fingerprint] = (job, result)  # 오름차순이므로 마지막이 최신
    return latest


def to_summary(group: ErrorGroup, latest: dict[str, JobRow]) -> ErrorGroupSummary:
    """그룹 ORM → 목록 항목. 분석 상태는 fingerprint 기준으로 붙인다."""
    job, result = latest.get(group.fingerprint, (None, None))
    return ErrorGroupSummary(
        id=group.id,
        query_run_id=group.query_run_id,
        fingerprint=group.fingerprint,
        service=group.service,
        environment=group.environment,
        error_type=group.error_type,
        normalized_message=group.normalized_message,
        count=group.count,
        first_seen=group.first_seen,
        last_seen=group.last_seen,
        analysis_status=job.status if job is not None else None,
        latest_analysis_job_id=job.id if job is not None else None,
        latest_severity=result.severity if result is not None else None,
    )


def summarize_groups(db: Session, groups: Sequence[ErrorGroup]) -> list[ErrorGroupSummary]:
    latest = latest_analysis_by_fingerprint(db, [group.fingerprint for group in groups])
    return [to_summary(group, latest) for group in groups]


def list_error_groups(
    db: Session, run_id: int, *, limit: int = 50, offset: int = 0
) -> ErrorGroupListResponse:
    if db.get(QueryRun, run_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"조회 이력 {run_id} 을(를) 찾을 수 없습니다.",
        )

    total = (
        db.scalar(select(func.count(ErrorGroup.id)).where(ErrorGroup.query_run_id == run_id)) or 0
    )
    groups = (
        db.scalars(
            select(ErrorGroup)
            .where(ErrorGroup.query_run_id == run_id)
            .order_by(ErrorGroup.count.desc(), ErrorGroup.last_seen.desc(), ErrorGroup.id.asc())
            .offset(offset)
            .limit(limit)
        )
        .all()
    )
    return ErrorGroupListResponse(
        query_run_id=run_id, total=total, items=summarize_groups(db, list(groups))
    )


def analysis_history(db: Session, fingerprint: str) -> list[AnalysisJobSummary]:
    """같은 fingerprint 의 과거 분석 이력 (최신순)."""
    rows = _analysis_rows(db, [fingerprint])
    rows.reverse()
    return [
        AnalysisJobSummary(
            id=job.id,
            status=job.status,
            provider=job.provider,
            model=job.model,
            prompt_version=job.prompt_version,
            requested_at=job.requested_at,
            completed_at=job.completed_at,
            severity=result.severity if result is not None else None,
            summary=result.summary if result is not None else None,
        )
        for job, result in rows
    ]


def get_error_group(db: Session, group_id: int) -> ErrorGroupDetail:
    group = db.get(ErrorGroup, group_id)
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"오류 그룹 {group_id} 을(를) 찾을 수 없습니다.",
        )

    summary = to_summary(group, latest_analysis_by_fingerprint(db, [group.fingerprint]))
    samples = db.scalars(
        select(ErrorSample)
        .where(ErrorSample.error_group_id == group.id)
        .order_by(ErrorSample.occurred_at.asc(), ErrorSample.id.asc())
    ).all()

    return ErrorGroupDetail(
        **summary.model_dump(),
        labels=dict(group.labels or {}),
        top_stack_frame=group.top_stack_frame,
        normalization_rule_version=group.normalization_rule_version,
        # 저장된 로그는 마스킹된 값뿐이다.
        samples=[ErrorSampleRead.model_validate(sample) for sample in samples],
        # 발생 추이는 metric 쿼리 기반이라 대시보드 경로에서 제공한다 (여기서는 비운다).
        trend=[],
        analyses=analysis_history(db, group.fingerprint),
    )


__all__ = [
    "analysis_history",
    "get_error_group",
    "latest_analysis_by_fingerprint",
    "list_error_groups",
    "summarize_groups",
    "to_summary",
]
