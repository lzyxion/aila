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
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.report import selector_from_labels
from app.enums import ACTIVE_JOB_STATUSES, TriggeredBy
from app.models import (
    AnalysisJob,
    AnalysisPolicy,
    AnalysisResult,
    ErrorGroup,
    ErrorSample,
    LokiConnection,
    QueryRun,
)
from app.policies import integrations as policy_integrations
from app.schemas.api import (
    AnalysisJobSummary,
    ErrorGroupDetail,
    ErrorGroupListResponse,
    ErrorGroupSummary,
    ErrorSampleRead,
)
from app.schemas.logrecord import CountPoint, FetchWarning, TimeRange

#: (job, result) 쌍. result 는 아직 완료되지 않은 작업이면 None 이다.
JobRow = tuple[AnalysisJob, AnalysisResult | None]

#: 진행 중으로 보는 상태 값 집합 (DB 에는 문자열이 들어 있다).
ACTIVE_STATUS_VALUES: frozenset[str] = frozenset(
    job_status.value for job_status in ACTIVE_JOB_STATUSES
)

# --------------------------------------------------------------- 발생 추이

#: 그룹 발생 구간 앞뒤로 붙이는 여유 (경계 버킷이 잘리지 않게).
TREND_MARGIN = timedelta(minutes=5)
#: 추이 포인트 수 목표 — step 은 이 값에 맞춰 정한다.
TREND_TARGET_POINTS = 60
TREND_MIN_STEP_SECONDS = 15
TREND_MAX_STEP_SECONDS = 3600

#: 그룹에 재조회용 라벨이 없어 selector 를 만들 수 없다.
WARN_TREND_NO_LABELS = "trend_no_labels"
#: 정책/연결이 사라졌거나 비활성이다.
WARN_TREND_UNAVAILABLE = "trend_connection_unavailable"
#: 어댑터가 건수 metric 을 지원하지 않는다.
WARN_TREND_UNSUPPORTED = "trend_count_unsupported"
#: metric 쿼리 자체가 실패했다.
WARN_TREND_FAILED = "trend_query_failed"


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


def active_analysis_by_fingerprint(
    db: Session, fingerprints: Iterable[str]
) -> dict[str, AnalysisJob]:
    """fingerprint -> **진행 중(pending/running)** 인 분석 작업.

    "이미 분석을 시작했는가"(=중복 과금 방지)를 판정하는 유일한 경로다.
    `latest_analysis_by_fingerprint` 와 분리한 이유는, 최신 1 건만 보면 그 뒤에
    실패한 작업이 하나 끼어드는 순간 **아직 돌고 있는 작업을 놓치기** 때문이다
    (실패 재시도 직후 두 번 누르면 그대로 이중 호출이 된다).
    """
    unique = list(dict.fromkeys(fingerprints))
    if not unique:
        return {}
    rows = db.scalars(
        select(AnalysisJob)
        .where(
            AnalysisJob.fingerprint.in_(unique),
            AnalysisJob.status.in_(sorted(ACTIVE_STATUS_VALUES)),
        )
        .order_by(AnalysisJob.requested_at.asc(), AnalysisJob.id.asc())
    ).all()
    active: dict[str, AnalysisJob] = {}
    for job in rows:
        active.setdefault(job.fingerprint, job)  # 가장 먼저 시작된 작업을 재사용한다
    return active


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
            triggered_by=job.triggered_by or TriggeredBy.MANUAL.value,
        )
        for job, result in rows
    ]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _trend_range_and_step(group: ErrorGroup) -> tuple[TimeRange, int]:
    """그룹 발생 구간(여유 포함)과 그 구간에 맞는 step 을 정한다."""
    start = _as_utc(group.first_seen) - TREND_MARGIN
    end = _as_utc(group.last_seen) + TREND_MARGIN
    span = max(1, int((end - start).total_seconds()))
    step = max(
        TREND_MIN_STEP_SECONDS, min(TREND_MAX_STEP_SECONDS, span // TREND_TARGET_POINTS or 1)
    )
    return TimeRange(start=start, end=end), step


def group_trend(db: Session, group: ErrorGroup) -> tuple[list[CountPoint], list[FetchWarning]]:
    """그룹 라벨 selector 로 `count_over_time` 을 걸어 발생 추이를 채운다.

    건수·추이는 저장된 라인을 세지 않고 **metric 쿼리**로 구한다 (계약). 저장된
    `error_samples` 는 그룹당 최대 3 건이라 추이로 쓸 수 없고, `error_groups.count` 는
    조회 상한에 잘린 라인 수다.

    실패는 절대 상세 화면을 죽이지 않는다 — 빈 배열과 **사유 경고**를 함께 돌려준다.
    조용히 빈 배열만 주면 "오류가 없었다"와 "조회하지 못했다"가 구분되지 않는다.
    """
    labels = {key: str(value) for key, value in (group.labels or {}).items() if value}
    selector = selector_from_labels(labels)
    if selector == "{}":
        return [], [
            FetchWarning(
                code=WARN_TREND_NO_LABELS,
                message="그룹에 재조회용 라벨이 없어 발생 추이를 계산할 수 없습니다.",
            )
        ]

    run = db.get(QueryRun, group.query_run_id)
    policy = db.get(AnalysisPolicy, run.policy_id) if run is not None else None
    connection = (
        db.get(LokiConnection, policy.loki_connection_id) if policy is not None else None
    )
    if connection is None or not connection.active:
        return [], [
            FetchWarning(
                code=WARN_TREND_UNAVAILABLE,
                message="이 그룹의 로그 소스 연결을 쓸 수 없어 발생 추이를 계산하지 못했습니다.",
            )
        ]

    time_range, step = _trend_range_and_step(group)
    try:
        provider = policy_integrations.build_provider(connection)
        if not getattr(provider, "supports_count", True):
            return [], [
                FetchWarning(
                    code=WARN_TREND_UNSUPPORTED,
                    message="이 로그 소스 어댑터는 건수·추이 metric 쿼리를 지원하지 않습니다.",
                )
            ]
        series = provider.count_over_time(selector, time_range, step)
    except Exception as exc:  # noqa: BLE001 - 추이 실패로 상세 화면을 죽이지 않는다
        return [], [
            FetchWarning(code=WARN_TREND_FAILED, message=f"{type(exc).__name__}: {exc}")
        ]

    return list(series.points), list(series.warnings)


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

    trend, trend_warnings = group_trend(db, group)

    return ErrorGroupDetail(
        **summary.model_dump(),
        labels=dict(group.labels or {}),
        top_stack_frame=group.top_stack_frame,
        normalization_rule_version=group.normalization_rule_version,
        # 저장된 로그는 마스킹된 값뿐이다.
        samples=[ErrorSampleRead.model_validate(sample) for sample in samples],
        # 발생 추이는 metric 쿼리 기반이다 (저장된 라인 수가 아니다).
        trend=trend,
        trend_warnings=trend_warnings,
        analyses=analysis_history(db, group.fingerprint),
    )


__all__ = [
    "WARN_TREND_FAILED",
    "WARN_TREND_NO_LABELS",
    "WARN_TREND_UNAVAILABLE",
    "WARN_TREND_UNSUPPORTED",
    "active_analysis_by_fingerprint",
    "analysis_history",
    "get_error_group",
    "group_trend",
    "latest_analysis_by_fingerprint",
    "list_error_groups",
    "summarize_groups",
    "to_summary",
]
