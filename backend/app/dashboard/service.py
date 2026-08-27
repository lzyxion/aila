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

Phase 7 에서 세 가지가 붙었다.

- `series` 는 **시각별로 접어서** 싣는다 (`counting.fold_by_timestamp`). 접기 전
  포인트에는 서비스 라벨이 남아 있으므로 `by_service` 를 **먼저** 계산한다.
- `group_count`·`unanalyzed_group_count` 는 회차 전체의 DB COUNT 다 —
  `top_groups`(상위 N) 길이를 지표 자리에 쓰면 그룹이 50 개여도 "10" 이 뜬다.
- 정책에 `baseline_query` 가 있으면 **같은 기간·step 으로 한 번 더** metric 을 걸어
  유입량(`ingest_total`)과 오류 비율(`error_ratio`)을 낸다. 미설정·실패는 `null` 이다
  (0 은 "유입이 없었다"로 읽힌다). 홈 화면(`summary`)에는 넣지 않는다 — 정책마다
  Loki 호출이 두 배가 되기 때문이다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dashboard.counting import fold_by_timestamp, group_count, unanalyzed_group_count
from app.enums import QueryRunStatus
from app.error_groups import service as error_group_service
from app.models import AnalysisPolicy, ErrorGroup, LokiConnection, QueryRun
from app.policies import integrations
from app.policies.service import HTTP_422, as_utc
from app.providers.logsource import LogSourceProvider
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
#: metric 시리즈가 서비스 라벨을 주지 못해 저장된 그룹(=잘린 라인) 집계로 대체했다.
WARN_BY_SERVICE_FROM_LINES = "by_service_from_lines"
#: 포인트 수가 너무 많아 step 을 자동으로 올렸다.
WARN_STEP_RAISED = "step_raised"
#: 정책의 `baseline_query`(분모) 실행이 실패했다 — 유입량·비율은 null 이 된다.
WARN_BASELINE_FAILED = "baseline_query_failed"

#: 기간 파라미터도 조회 이력도 없을 때의 기본 조회 구간(분).
DEFAULT_RANGE_MINUTES = 60

#: step 하한·상한 (라우터의 Query 제약과 같은 값).
MIN_STEP_SECONDS = 15
MAX_STEP_SECONDS = 3600
#: 한 응답에 실을 수 있는 시리즈 포인트 수의 상한.
MAX_SERIES_POINTS = 1000


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


def _ceil_to_step(moment: datetime, step_seconds: int) -> datetime:
    """`moment` 를 step 경계로 **올림**한다.

    Loki 는 `query_range` 의 버킷을 step 배수에 정렬한다. 그래서 `range_end` 가 step
    경계에 걸치지 않으면 **마지막 버킷이 통째로 빠진다** — 기간을 지정하지 않고 최근
    조회 이력(`query_runs.range_end`)을 그대로 쓰는 대시보드에서는 그 누락이 영구적이라,
    "방금 터진 오류"가 추이에 영영 나타나지 않는다.
    """
    step = max(1, int(step_seconds))
    return datetime.fromtimestamp(math.ceil(moment.timestamp() / step) * step, tz=UTC)


def _resolve_step(
    *, range_start: datetime, range_end: datetime, step_seconds: int
) -> tuple[int, list[FetchWarning]]:
    """step 을 하한·상한 안으로 넣고, 포인트 수가 너무 많으면 자동으로 올린다."""
    warnings: list[FetchWarning] = []
    step = min(MAX_STEP_SECONDS, max(MIN_STEP_SECONDS, int(step_seconds)))

    span_seconds = max(1, int((range_end - range_start).total_seconds()))
    if span_seconds / step > MAX_SERIES_POINTS:
        raised = min(
            MAX_STEP_SECONDS, max(step, math.ceil(span_seconds / MAX_SERIES_POINTS))
        )
        if raised > step:
            warnings.append(
                FetchWarning(
                    code=WARN_STEP_RAISED,
                    message=(
                        f"요청 step {step} 초로는 포인트가 {MAX_SERIES_POINTS} 개를 넘어 "
                        f"{raised} 초로 올렸습니다."
                    ),
                    count=raised,
                )
            )
            step = raised
    return step, warnings


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


@dataclass
class _MetricResult:
    """오류 metric 쿼리 1 회의 결과 묶음.

    `points` 는 **접기 전** 포인트다 (서비스 라벨이 붙어 있다). `provider` 를 함께
    들고 나오는 것은 분모 쿼리(`baseline_query`)를 같은 어댑터로 한 번 더 걸기
    위해서다 — 두 번 만들면 같은 화면의 두 숫자가 서로 다른 연결 상태를 볼 수 있다.
    """

    points: list[CountPoint] = field(default_factory=list)
    total: float = 0.0
    step_seconds: int = 300
    service_label: str = "service"
    provider: LogSourceProvider | None = None
    warnings: list[FetchWarning] = field(default_factory=list)


def _count_series(
    db: Session,
    *,
    policy: AnalysisPolicy | None,
    range_start: datetime,
    range_end: datetime,
    step_seconds: int,
) -> _MetricResult:
    """`count_over_time` metric 쿼리. 실패는 경고로 강등하고 화면을 살린다.

    `service_label` 은 시리즈에서 **서비스를 가리키는 라벨 이름**이다 — 어댑터가
    `sum by (<라벨>)` 로 감싸므로, 소스 라벨 이름이 `service` 가 아닐 수 있다.
    """
    warnings: list[FetchWarning] = []
    if policy is None:
        warnings.append(
            FetchWarning(
                code=WARN_NO_POLICY,
                message="정책 또는 조회 이력이 없어 오류 추이를 계산할 수 없습니다.",
            )
        )
        return _MetricResult(step_seconds=step_seconds, warnings=warnings)

    connection = db.get(LokiConnection, policy.loki_connection_id)
    if connection is None or not connection.active:
        warnings.append(
            FetchWarning(
                code=WARN_CONNECTION_UNAVAILABLE,
                message=f"정책 '{policy.name}' 의 로그 소스 연결을 쓸 수 없습니다.",
            )
        )
        return _MetricResult(step_seconds=step_seconds, warnings=warnings)

    provider: LogSourceProvider | None = None
    service_label = "service"
    try:
        provider = integrations.build_provider(connection)
        service_label = str(getattr(provider, "service_label", None) or "service")
        if not getattr(provider, "supports_count", True):
            warnings.append(
                FetchWarning(
                    code=WARN_COUNT_UNSUPPORTED,
                    message="이 로그 소스 어댑터는 건수·추이 metric 쿼리를 지원하지 않습니다.",
                )
            )
            return _MetricResult(
                step_seconds=step_seconds,
                service_label=service_label,
                provider=provider,
                warnings=warnings,
            )
        series = provider.count_over_time(
            policy.logql, TimeRange(start=range_start, end=range_end), step_seconds
        )
    except Exception as exc:  # noqa: BLE001 - 추이 실패로 대시보드 전체를 죽이지 않는다
        warnings.append(
            FetchWarning(code=WARN_COUNT_FAILED, message=f"{type(exc).__name__}: {exc}")
        )
        return _MetricResult(
            step_seconds=step_seconds,
            service_label=service_label,
            provider=provider,
            warnings=warnings,
        )

    warnings.extend(series.warnings)
    return _MetricResult(
        points=list(series.points),
        total=series.total,
        step_seconds=series.step_seconds or step_seconds,
        service_label=service_label,
        provider=provider,
        warnings=warnings,
    )


@dataclass
class _Baseline:
    """분모 쿼리 결과. 세 값 모두 "계산하지 않았다" 를 `None`/빈 배열로 표현한다."""

    total: float | None = None
    series: list[CountPoint] = field(default_factory=list)
    ratio: float | None = None
    warnings: list[FetchWarning] = field(default_factory=list)


def _baseline_metrics(
    *,
    policy: AnalysisPolicy | None,
    provider: LogSourceProvider | None,
    range_start: datetime,
    range_end: datetime,
    step_seconds: int,
    total_errors: float,
) -> _Baseline:
    """정책의 `baseline_query` 로 유입량·오류 비율을 낸다 (metric 쿼리 1 회 추가).

    분모 쿼리를 오류 쿼리에서 역산하지 않는 이유는 하나다 — 셀렉터를 기계적으로
    벗겨 내면 대부분의 경우 조용히 다른 범위를 세게 되고, 틀린 비율은 없느니만
    못하다. 그래서 운영자가 명시한 쿼리가 없으면 **계산하지 않는다**(`null`).

    실패는 `baseline_query_failed` 경고로 강등하고 세 값을 비운다. 분모 하나 때문에
    상위 그룹·추이까지 사라지면, 정확히 그 화면을 보러 온 사람이 아무것도 못 본다.
    """
    query = (policy.baseline_query or "").strip() if policy is not None else ""
    if not query:
        return _Baseline()

    if provider is None or not getattr(provider, "supports_count", True):
        return _Baseline(
            warnings=[
                FetchWarning(
                    code=WARN_BASELINE_FAILED,
                    message="분모 쿼리를 실행할 수 있는 metric 지원 어댑터가 없습니다.",
                )
            ]
        )

    try:
        series = provider.count_over_time(
            query, TimeRange(start=range_start, end=range_end), step_seconds
        )
    except Exception as exc:  # noqa: BLE001 - 분모 실패로 화면을 죽이지 않는다
        return _Baseline(
            warnings=[
                FetchWarning(
                    code=WARN_BASELINE_FAILED, message=f"{type(exc).__name__}: {exc}"
                )
            ]
        )

    # 분모 쿼리 자체의 경고(예: empty_result)는 올리지 않는다 — 오류 metric 의 같은
    # 코드와 섞이면 화면에서 "무엇이 비었는지" 를 구분할 수 없다.
    total = float(series.total)
    ratio = (total_errors / total) if total > 0 else None
    return _Baseline(total=total, series=fold_by_timestamp(series.points), ratio=ratio)


def _by_service_from_points(
    points: list[CountPoint], service_label: str = "service"
) -> list[ServiceErrorCount]:
    """시리즈 라벨로 서비스별 건수를 집계한다.

    라벨 이름은 소스마다 다르므로(`app`, `service_name` …) 연결 매핑이 알려준 이름을
    먼저 보고, 없으면 표준 이름 `service` 로 되짚는다.
    """
    totals: dict[str | None, float] = {}
    for point in points:
        service = point.labels.get(service_label) or point.labels.get("service")
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

    step, step_warnings = _resolve_step(
        range_start=start, range_end=end, step_seconds=step_seconds
    )
    warnings.extend(step_warnings)
    # step 경계로 올려야 마지막 버킷이 빠지지 않는다 (기간 미지정 시 특히).
    end = _ceil_to_step(end, step)

    metric = _count_series(
        db, policy=policy, range_start=start, range_end=end, step_seconds=step
    )
    warnings.extend(metric.warnings)

    # 서비스별 분해는 **접기 전** 포인트로 먼저 계산한다 — 접으면 라벨이 사라진다.
    by_service = _by_service_from_points(metric.points, metric.service_label)
    if not any(item.service for item in by_service) and run is not None:
        # metric 이 라벨을 주지 못했다 -> 저장된 그룹(=상한에 잘린 라인) 집계로 대체한다.
        # 조용히 넘어가면 화면의 숫자가 metric 기준인지 라인 기준인지 알 수 없다.
        by_service = _by_service_from_db(db, run.id)
        if by_service:
            warnings.append(
                FetchWarning(
                    code=WARN_BY_SERVICE_FROM_LINES,
                    message=(
                        "metric 쿼리가 서비스 라벨을 주지 않아 서비스별 건수를 저장된 "
                        "그룹(조회 상한에 걸릴 수 있는 라인 기준)으로 대체했습니다."
                    ),
                )
            )

    baseline = _baseline_metrics(
        policy=policy,
        provider=metric.provider,
        range_start=start,
        range_end=end,
        step_seconds=step,
        total_errors=metric.total,
    )
    warnings.extend(baseline.warnings)

    top_groups = []
    # 회차가 없으면 두 COUNT 는 0 이 아니라 **null** 이다 — 0 은 "그룹이 없었다"로
    # 읽히고, 그러면 "아직 한 번도 돌지 않았다" 와 구분되지 않는다.
    total_groups: int | None = None
    unanalyzed: int | None = None
    if run is not None:
        groups = db.scalars(
            select(ErrorGroup)
            .where(ErrorGroup.query_run_id == run.id)
            .order_by(ErrorGroup.count.desc(), ErrorGroup.last_seen.desc(), ErrorGroup.id.asc())
            .limit(top)
        ).all()
        top_groups = error_group_service.summarize_groups(db, list(groups))
        total_groups = group_count(db, run.id)
        unanalyzed = unanalyzed_group_count(db, run.id)

    return DashboardOverviewResponse(
        policy_id=policy.id if policy is not None else None,
        query_run_id=run.id if run is not None else None,
        range_start=start,
        range_end=end,
        step_seconds=metric.step_seconds,
        total_errors=metric.total,
        # 같은 시각의 서비스별 점을 하나로 접는다 (`by_service` 는 위에서 이미 계산했다).
        series=fold_by_timestamp(metric.points),
        by_service=by_service,
        top_groups=top_groups,
        group_count=total_groups,
        unanalyzed_group_count=unanalyzed,
        ingest_total=baseline.total,
        ingest_series=baseline.series,
        error_ratio=baseline.ratio,
        warnings=warnings,
    )


__all__ = [
    "MAX_SERIES_POINTS",
    "MAX_STEP_SECONDS",
    "MIN_STEP_SECONDS",
    "WARN_BASELINE_FAILED",
    "WARN_BY_SERVICE_FROM_LINES",
    "WARN_CONNECTION_UNAVAILABLE",
    "WARN_COUNT_FAILED",
    "WARN_COUNT_UNSUPPORTED",
    "WARN_NO_POLICY",
    "WARN_RANGE_CLAMPED",
    "WARN_STEP_RAISED",
    "get_overview",
]
