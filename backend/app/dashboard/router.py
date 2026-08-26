"""`/api/dashboard/overview` 라우터.

Phase 1 담당 트랙: **정책 API**

계약상 제약: 건수·추이는 로그 라인을 세지 않고 `count_over_time` metric 쿼리로 구한다.
라인 조회는 상한에 걸려 오류 폭증 시 실제보다 적게 나온다.
상위 그룹은 저장된 그룹화 결과(DB)에서 집계하고, 분석 상태는 fingerprint 기준이다.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dashboard import (
    error_groups as error_group_feed,
    service,
    summary as summary_service,
)
from app.db import get_db
from app.schemas.api import (
    DashboardErrorGroupListResponse,
    DashboardOverviewResponse,
    DashboardSummaryResponse,
)

TRACK = "정책 API"

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryResponse)
def get_summary(db: Session = Depends(get_db)) -> DashboardSummaryResponse:
    """정책 전체 요약 (Phase 5). 정책 상세 뷰는 `/overview` 가 그대로 담당한다.

    정책 수만큼 metric 호출이 나가므로 정책별 타임아웃·실패 격리가 걸려 있다 —
    한 정책의 Loki 가 죽어도 나머지 줄은 그대로 나온다 (`total_errors_24h=null`
    + 경고 코드).
    """
    return summary_service.get_summary(db)


@router.get("/error-groups", response_model=DashboardErrorGroupListResponse)
def list_error_groups(
    limit: int = Query(default=50, gt=0, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> DashboardErrorGroupListResponse:
    """전 활성 정책의 **최신 성공 조회** 그룹을 한데 모은 목록 (Phase 6).

    정책 하나의 그룹 목록은 기존 `/query-runs/{id}/error-groups` 가 그대로 담당한다.
    여기서는 항목마다 `policy_id`·`policy_name` 이 붙어 출처를 되짚을 수 있다.
    """
    return error_group_feed.list_error_groups(db, limit=limit, offset=offset)


@router.get("/overview", response_model=DashboardOverviewResponse)
def get_overview(
    policy_id: int | None = None,
    query_run_id: int | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    step_seconds: int = Query(
        default=300,
        ge=service.MIN_STEP_SECONDS,
        le=service.MAX_STEP_SECONDS,
        description=(
            "metric 쿼리 step(초). 포인트 수가 "
            f"{service.MAX_SERIES_POINTS} 개를 넘으면 서버가 자동으로 올리고 경고를 남긴다."
        ),
    ),
    top: int = Query(default=10, gt=0, le=100),
    db: Session = Depends(get_db),
) -> DashboardOverviewResponse:
    return service.get_overview(
        db,
        policy_id=policy_id,
        query_run_id=query_run_id,
        range_start=range_start,
        range_end=range_end,
        step_seconds=step_seconds,
        top=top,
    )
