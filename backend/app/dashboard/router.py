"""`/api/dashboard/overview` 라우터 (스켈레톤).

Phase 1 담당 트랙: **정책 API**

계약상 제약: 건수·추이는 로그 라인을 세지 않고 `count_over_time` metric 쿼리로 구한다.
라인 조회는 상한에 걸려 오류 폭증 시 실제보다 적게 나온다.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.api import DashboardOverviewResponse
from app.stub import not_implemented

TRACK = "정책 API"

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview", response_model=DashboardOverviewResponse)
def get_overview(
    policy_id: int | None = None,
    query_run_id: int | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    step_seconds: int = Query(default=300, gt=0),
    top: int = Query(default=10, gt=0, le=100),
    db: Session = Depends(get_db),
) -> DashboardOverviewResponse:
    not_implemented(TRACK)
