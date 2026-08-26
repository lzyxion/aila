"""대시보드 개요 서비스 (오류 추이 · 서비스별 건수 · 상위 그룹).

Phase 1 담당 트랙: **정책 API**

계약상 제약:
- 건수·추이는 저장된 로그 라인을 세지 않고 `count_over_time` **metric 쿼리**로 구한다.
  라인 조회에는 정책 상한과 소스 자체 한도가 걸려 있어, 오류가 폭증하는 순간
  — 정확히 이 화면이 필요한 순간 — 라인 기반 집계는 실제보다 적게 나온다.
- 상위 그룹은 이미 저장된 그룹화 결과(DB)에서 집계한다.
- 분석 상태는 그룹 id 가 아니라 **fingerprint 기준**이다.

metric 쿼리가 실패해도 화면 전체를 죽이지 않는다 — 상위 그룹은 DB 에서 나오므로
경고(`count_query_failed`)를 붙이고 나머지를 그대로 준다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.enums import QueryRunStatus
from app.error_groups import service as error_group_service
from app.models import AnalysisPolicy, ErrorGroup, LokiConnection, QueryRun
from app.policies import integrations
from app.policies.service import HTTP_422, as_utc
from app.schemas.api import DashboardOverviewResponse, ServiceErrorCount
from app.schemas.logrecord import CountPoint, FetchWarning, TimeRange

#: 정책도 조회 이력도 없어 metric 쿼리를 걸 대상이 없다.
WARN_NO_POLICY = "no_policy"
#: 정책이 참조하는 연결이 사라졌거나 비활성이다.
WARN_CONNECTION_UNAVAILABLE = "connection_unavailable"
#: 어댑터가 `supports_count=False` 라 추이를 줄 수 없다.
WARN_COUNT_UNSUPPORTED = "count_unsupported"
#: metric 쿼리 자체가 실패했다.
WARN_COUNT_FAILED = "count_query_failed"
#: 요청 기간이 서버 상한으로 조정되었다.
WARN_RANGE_CLAMPED = "range_clamped"

#: 기간 파라미터도 조회 이력도 없을 때의 기본 조회 구간(분).
DEFAULT_RANGE_MINUTES = 60


def _latest_run(db: Session, policy_id: int | None) -> QueryRun | None:
    """가장 최근 조회 이력. 성공한 조회를 우선한다."""
    base = select(QueryRun).order_by(QueryRun.started_at.desc(), QueryRun.id.desc())
    if policy_id is not None:
        base = base.where(QueryRun.policy_id == policy_id)
    succeeded = db.scalars(
        base.where(QueryRun.status == QueryRunStatus.SUCCEEDED.value).limit(1)
    ).first()
    return succeeded or db.scalars(base.limit(1)).first()


def _resolve_scope(
    db: Session, policy_id: int | None, query_run_id: int | None
) -> tuple[AnalysisPolicy | None, QueryRun | None]:
    if query_run_id is not None:
        run = db.get(QueryRun, query_run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"조회 이력 {query_run_id} 을(를) 찾을 수 없습니다.",
            )
        return db.get(AnalysisPolicy, run.policy_id), run

    if policy_id is not None:
        policy = db.get(AnalysisPolicy, policy_id)
        if policy is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"정책 {policy_id} 을(를) 찾을 수 없습니다.",
            )
        return policy, _latest_run(db, policy.id)

    run = _latest_run(db, None)
    policy = db.get(AnalysisPolicy, run.policy_id) if run is not None else None
    return policy, run


def _resolve_range(
    *,
    policy: AnalysisPolicy | None,
    run: QueryRun | None,
    range_start: datetime | None,
    range_end: datetime | None,
) -> tuple[datetime, datetime, list[FetchWarning]]:
    settings = get_settings()
    warnings: list[FetchWarning] = []

    if range_end is not None:
        end = as_utc(range_end)
    elif run is not None:
        end = as_utc(run.range_end)
    else:
        end = datetime.now(UTC)

    if range_start is not None:
        start = as_utc(range_start)
    elif run is not None and range_end is None:
        start = as_utc(run.range_start)
    else:
        minutes = policy.default_range_minutes if policy is not None else DEFAULT_RANGE_MINUTES
        start = end - timedelta(minutes=minutes)

    if start >= end:
        raise HTTPException(
            status_code=HTTP_422, detail="range_start 는 range_end 보다 앞이어야 합니다."
        )

    span = timedelta(minutes=settings.max_query_range_minutes)
    if end - start > span:
        start = end - span
        warnings.append(
            FetchWarning(
                code=WARN_RANGE_CLAMPED,
                message=(
                    f"요청 기간이 서버 상한 {settings.max_query_range_minutes} 분으로 "
                    f"조정되었습니다 (range_start={start.isoformat()})."
                ),
            )
        )
    return start, end, warnings


def _count_series(
    db: Session,
    *,
    policy: AnalysisPolicy | None,
    range_start: datetime,
    range_end: datetime,
    step_seconds: int,
) -> tuple[list[CountPoint], float, int, list[FetchWarning]]:
    """`count_over_time` metric 쿼리. 실패는 경고로 강등하고 화면을 살린다."""
    warnings: list[FetchWarning] = []
    if policy is None:
        warnings.append(
            FetchWarning(
                code=WARN_NO_POLICY,
                message="정책 또는 조회 이력이 없어 오류 추이를 계산할 수 없습니다.",
            )
        )
        return [], 0.0, step_seconds, warnings

    connection = db.get(LokiConnection, policy.loki_connection_id)
    if connection is None or not connection.active:
        warnings.append(
            FetchWarning(
                code=WARN_CONNECTION_UNAVAILABLE,
                message=f"정책 '{policy.name}' 의 로그 소스 연결을 쓸 수 없습니다.",
            )
        )
        return [], 0.0, step_seconds, warnings

    try:
        provider = integrations.build_provider(connection)
        if not getattr(provider, "supports_count", True):
            warnings.append(
                FetchWarning(
                    code=WARN_COUNT_UNSUPPORTED,
                    message="이 로그 소스 어댑터는 건수·추이 metric 쿼리를 지원하지 않습니다.",
                )
            )
            return [], 0.0, step_seconds, warnings
        series = provider.count_over_time(
            policy.logql, TimeRange(start=range_start, end=range_end), step_seconds
        )
    except Exception as exc:  # noqa: BLE001 - 추이 실패로 대시보드 전체를 죽이지 않는다
        warnings.append(
            FetchWarning(code=WARN_COUNT_FAILED, message=f"{type(exc).__name__}: {exc}")
        )
        return [], 0.0, step_seconds, warnings

    warnings.extend(series.warnings)
    return list(series.points), series.total, series.step_seconds or step_seconds, warnings


def _by_service_from_points(points: list[CountPoint]) -> list[ServiceErrorCount]:
    totals: dict[str | None, float] = {}
    for point in points:
        service = point.labels.get("service")
        totals[service] = totals.get(service, 0.0) + point.value
    return [
        ServiceErrorCount(service=service, count=count)
        for service, count in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]


def _by_service_from_db(db: Session, run_id: int) -> list[ServiceErrorCount]:
    """metric 쿼리가 서비스 라벨을 주지 못할 때의 대체 집계 (저장된 그룹 기준)."""
    rows = db.execute(
        select(ErrorGroup.service, func.sum(ErrorGroup.count))
        .where(ErrorGroup.query_run_id == run_id)
        .group_by(ErrorGroup.service)
        .order_by(func.sum(ErrorGroup.count).desc())
    ).all()
    return [ServiceErrorCount(service=service, count=float(total or 0)) for service, total in rows]


def get_overview(
    db: Session,
    *,
    policy_id: int | None = None,
    query_run_id: int | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    step_seconds: int = 300,
    top: int = 10,
) -> DashboardOverviewResponse:
    policy, run = _resolve_scope(db, policy_id, query_run_id)
    start, end, warnings = _resolve_range(
        policy=policy, run=run, range_start=range_start, range_end=range_end
    )

    points, total, resolved_step, count_warnings = _count_series(
        db, policy=policy, range_start=start, range_end=end, step_seconds=step_seconds
    )
    warnings.extend(count_warnings)

    by_service = _by_service_from_points(points)
    if not any(item.service for item in by_service) and run is not None:
        by_service = _by_service_from_db(db, run.id)

    top_groups = []
    if run is not None:
        groups = db.scalars(
            select(ErrorGroup)
            .where(ErrorGroup.query_run_id == run.id)
            .order_by(ErrorGroup.count.desc(), ErrorGroup.last_seen.desc(), ErrorGroup.id.asc())
            .limit(top)
        ).all()
        top_groups = error_group_service.summarize_groups(db, list(groups))

    return DashboardOverviewResponse(
        policy_id=policy.id if policy is not None else None,
        query_run_id=run.id if run is not None else None,
        range_start=start,
        range_end=end,
        step_seconds=resolved_step,
        total_errors=total,
        series=points,
        by_service=by_service,
        top_groups=top_groups,
        warnings=warnings,
    )


__all__ = [
    "WARN_CONNECTION_UNAVAILABLE",
    "WARN_COUNT_FAILED",
    "WARN_COUNT_UNSUPPORTED",
    "WARN_NO_POLICY",
    "WARN_RANGE_CLAMPED",
    "get_overview",
]
