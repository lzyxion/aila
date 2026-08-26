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

from app.dashboard import service
from app.db import get_db
from app.schemas.api import DashboardOverviewResponse

TRACK = "정책 API"

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


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
