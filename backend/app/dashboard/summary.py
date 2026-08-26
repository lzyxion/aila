"""`/api/dashboard/summary` — 정책 전체를 한 화면에 모은 운영 요약 (Phase 5).

`overview` 와의 분업이 이 모듈의 존재 이유다.
- `overview` 는 **정책 하나**의 추이·상위 그룹을 준다 (정책 상세 화면).
- `summary` 는 **모든 정책**의 상태 한 줄씩을 준다 (첫 화면). 추이 시리즈는 싣지 않는다.

>>> 실패 격리 <<<
정책 수만큼 `count_over_time` 호출이 나간다. 정책 하나의 Loki 가 죽어 있다고 화면
전체가 죽으면, 정확히 그 사실을 확인하러 들어온 사람이 아무것도 못 본다. 그래서
정책별로 예외를 삼키고 `total_errors_24h=None` + 경고 코드를 남긴다 —
**0 이 아니라 None** 인 것이 핵심이다 (0 은 "오류가 없었다"로 읽힌다).

한 정책의 metric 호출에 개별 시간 제한을 건다. 어댑터 자체 타임아웃
(`AILA_QUERY_TIMEOUT_SECONDS`)이 이미 있지만, 그것은 소켓 단위라 재시도·리다이렉트가
겹치면 벽시계 시간은 그보다 길어질 수 있다. 정책이 10 개면 그 누적이 화면 대기 시간이
되므로, 호출을 스레드로 돌리고 `SUMMARY_COUNT_TIMEOUT_SECONDS` 안에 안 오면 버린다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import QueryRunStatus
from app.models import AnalysisJob, AnalysisPolicy, ErrorGroup, LokiConnection, QueryRun
from app.policies import integrations
from app.schemas.api import (
    DashboardPolicySummary,
    DashboardSummaryLastRun,
    DashboardSummaryResponse,
)
from app.schemas.logrecord import FetchWarning, TimeRange

logger = logging.getLogger(__name__)

#: 24 시간 구간을 몇 초 버킷으로 볼 것인가. 합계만 쓰므로 굵게 잡아 응답을 가볍게 한다.
SUMMARY_STEP_SECONDS = 3600
#: 24 시간.
SUMMARY_RANGE = timedelta(hours=24)
#: 정책 하나의 metric 호출에 허용하는 벽시계 시간.
SUMMARY_COUNT_TIMEOUT_SECONDS = 15.0

#: 정책이 비활성이라 metric 을 걸지 않았다.
WARN_POLICY_INACTIVE = "policy_inactive"
#: 정책이 참조하는 연결이 사라졌거나 비활성이다.
WARN_CONNECTION_UNAVAILABLE = "connection_unavailable"
#: 어댑터가 건수 metric 을 지원하지 않는다.
WARN_COUNT_UNSUPPORTED = "count_unsupported"
#: metric 쿼리가 실패했다.
WARN_COUNT_FAILED = "count_query_failed"
#: 개별 시간 제한을 넘겨 버렸다.
WARN_COUNT_TIMEOUT = "count_query_timeout"
#: 성공한 조회 이력이 아직 없어 미분석 그룹 수를 셀 근거가 없다.
WARN_NO_SUCCESSFUL_RUN = "no_successful_run"


def _now() -> datetime:
    return datetime.now(UTC)


def _latest_run(db: Session, policy_id: int, *, succeeded_only: bool = False) -> QueryRun | None:
    stmt = (
        select(QueryRun)
        .where(QueryRun.policy_id == policy_id)
        .order_by(QueryRun.started_at.desc(), QueryRun.id.desc())
        .limit(1)
    )
    if succeeded_only:
        stmt = stmt.where(QueryRun.status == QueryRunStatus.SUCCEEDED.value)
    return db.scalars(stmt).first()


def _group_count(db: Session, run_id: int) -> int:
    return int(
        db.scalar(select(func.count(ErrorGroup.id)).where(ErrorGroup.query_run_id == run_id)) or 0
    )


def unanalyzed_group_count(db: Session, run_id: int) -> int:
    """`run_id` 의 그룹 중 **fingerprint 분석 이력이 전혀 없는** 그룹 수.

    판정 기준이 그룹 id 가 아니라 fingerprint 인 것이 핵심이다 — 그룹 id 는 조회
    회차마다 새로 생기므로, id 로 세면 어제 분석한 오류가 오늘도 "미분석" 으로 잡혀
    자동 분석이 매 회차 같은 오류를 다시 태운다.

    상태도 보지 않는다. 실패한 분석도 "이력이 있다" 로 친다 — 실패를 미분석으로 세면
    같은 실패를 자동 분석이 무한히 재시도한다.
    """
    exists = (
        select(AnalysisJob.id)
        .where(AnalysisJob.fingerprint == ErrorGroup.fingerprint)
        .exists()
    )
    return int(
        db.scalar(
            select(func.count(ErrorGroup.id)).where(
                ErrorGroup.query_run_id == run_id, ~exists
            )
        )
        or 0
    )


def _errors_24h(
    db: Session, policy: AnalysisPolicy
) -> tuple[float | None, list[FetchWarning]]:
    """정책별 최근 24 시간 오류 건수 (`count_over_time`). 실패는 `None` + 경고."""
    warnings: list[FetchWarning] = []
    if not policy.active:
        warnings.append(
            FetchWarning(
                code=WARN_POLICY_INACTIVE,
                message="비활성 정책이라 최근 24 시간 건수를 조회하지 않았습니다.",
            )
        )
        return None, warnings

    connection = db.get(LokiConnection, policy.loki_connection_id)
    if connection is None or not connection.active:
        warnings.append(
            FetchWarning(
                code=WARN_CONNECTION_UNAVAILABLE,
                message=f"정책 '{policy.name}' 의 로그 소스 연결을 쓸 수 없습니다.",
            )
        )
        return None, warnings

    end = _now()
    time_range = TimeRange(start=end - SUMMARY_RANGE, end=end)

    # 어댑터 생성은 **호출 스레드에서** 한다 — 팩토리가 ORM 속성(base_url·secret)을
    # 읽으므로 다른 스레드에서 만들면 세션을 스레드 밖에서 만지게 된다.
    try:
        provider = integrations.build_provider(connection)
    except Exception as exc:  # noqa: BLE001 - 연결 하나가 깨져도 화면은 살린다
        warnings.append(
            FetchWarning(code=WARN_COUNT_FAILED, message=f"{type(exc).__name__}: {exc}")
        )
        return None, warnings

    if not getattr(provider, "supports_count", True):
        warnings.append(
            FetchWarning(
                code=WARN_COUNT_UNSUPPORTED,
                message="이 로그 소스 어댑터는 건수 metric 쿼리를 지원하지 않습니다.",
            )
        )
        return None, warnings

    query = policy.logql

    def _call() -> tuple[float, list[FetchWarning]]:
        series = provider.count_over_time(query, time_range, SUMMARY_STEP_SECONDS)
        return float(series.total), list(series.warnings)

    # 정책 하나가 느리다고 나머지 정책의 응답까지 붙잡지 않게 벽시계 제한을 건다.
    # `shutdown(wait=False)` — 버린 호출이 끝나기를 기다리지 않는다 (어댑터 타임아웃이 끝낸다).
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(_call)
        try:
            total, call_warnings = future.result(timeout=SUMMARY_COUNT_TIMEOUT_SECONDS)
        except FutureTimeout:
            # 남은 스레드는 어댑터 타임아웃에 걸려 알아서 끝난다 (결과만 버린다).
            future.cancel()
            logger.warning(
                "dashboard summary: 정책 %s 의 24h 건수 조회가 시간 제한을 넘겼습니다.",
                policy.id,
            )
            warnings.append(
                FetchWarning(
                    code=WARN_COUNT_TIMEOUT,
                    message=(
                        f"최근 24 시간 건수 조회가 {SUMMARY_COUNT_TIMEOUT_SECONDS:.0f} 초 안에 "
                        "끝나지 않아 생략했습니다."
                    ),
                )
            )
            return None, warnings
        except Exception as exc:  # noqa: BLE001 - 정책 하나의 실패로 화면을 죽이지 않는다
            warnings.append(
                FetchWarning(code=WARN_COUNT_FAILED, message=f"{type(exc).__name__}: {exc}")
            )
            return None, warnings
    finally:
        pool.shutdown(wait=False)

    warnings.extend(call_warnings)
    return total, warnings


def _run_warnings(run: QueryRun) -> list[FetchWarning]:
    """저장된 warnings(JSON) → 스키마. 형식이 깨진 행 하나로 요약 전체를 죽이지 않는다."""
    parsed: list[FetchWarning] = []
    for item in run.warnings or []:
        try:
            parsed.append(FetchWarning.model_validate(item))
        except Exception:  # noqa: BLE001 - 옛 형식/손상 행 방어
            continue
    return parsed


def _last_run_payload(db: Session, run: QueryRun) -> DashboardSummaryLastRun:
    return DashboardSummaryLastRun(
        id=run.id,
        started_at=run.started_at,
        status=run.status,
        fetched_count=run.fetched_count,
        group_count=_group_count(db, run.id),
        warnings=_run_warnings(run),
    )


def get_summary(db: Session) -> DashboardSummaryResponse:
    """정책 전체 요약. 비활성 정책도 뺀 자리 없이 싣고 `active` 로 구분한다.

    목록에서 빼 버리면 "정책을 지웠나?" 와 "비활성인가?" 가 화면에서 구분되지 않는다.
    """
    policies = list(db.scalars(select(AnalysisPolicy).order_by(AnalysisPolicy.id.asc())).all())

    items: list[DashboardPolicySummary] = []
    for policy in policies:
        warnings: list[FetchWarning] = []

        last_run = _latest_run(db, policy.id)
        last_succeeded = (
            last_run
            if last_run is not None and last_run.status == QueryRunStatus.SUCCEEDED.value
            else _latest_run(db, policy.id, succeeded_only=True)
        )

        if last_succeeded is None:
            unanalyzed = 0
            warnings.append(
                FetchWarning(
                    code=WARN_NO_SUCCESSFUL_RUN,
                    message="성공한 조회 이력이 없어 미분석 그룹 수를 셀 수 없습니다.",
                )
            )
        else:
            unanalyzed = unanalyzed_group_count(db, last_succeeded.id)

        total_errors, count_warnings = _errors_24h(db, policy)
        warnings.extend(count_warnings)

        items.append(
            DashboardPolicySummary(
                policy_id=policy.id,
                name=policy.name,
                active=policy.active,
                schedule_enabled=policy.schedule_enabled,
                schedule_interval_minutes=policy.schedule_interval_minutes,
                last_run=(
                    _last_run_payload(db, last_run) if last_run is not None else None
                ),
                unanalyzed_group_count=unanalyzed,
                total_errors_24h=total_errors,
                warnings=warnings,
            )
        )

    return DashboardSummaryResponse(generated_at=_now(), policies=items)


__all__ = [
    "SUMMARY_COUNT_TIMEOUT_SECONDS",
    "SUMMARY_RANGE",
    "SUMMARY_STEP_SECONDS",
    "WARN_CONNECTION_UNAVAILABLE",
    "WARN_COUNT_FAILED",
    "WARN_COUNT_TIMEOUT",
    "WARN_COUNT_UNSUPPORTED",
    "WARN_NO_SUCCESSFUL_RUN",
    "WARN_POLICY_INACTIVE",
    "get_summary",
    "unanalyzed_group_count",
]
