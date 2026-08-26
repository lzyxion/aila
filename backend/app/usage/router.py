"""`/api/usage` 라우터.

Phase 2 담당 트랙: **분석 플로우·usage·보고서**

계약상 제약: `estimated_cost` 는 계산 시점 단가표 기준 **추정**이다. 사용량 대시보드는
사후 확인일 뿐이므로, 비용 차단은 일일 분석 한도가 담당한다.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.api import UsageResponse
from app.usage import service

TRACK = "LLM 분석"

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=UsageResponse)
def get_usage(
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    model: str | None = None,
    provider: str | None = None,
    group_by: str | None = Query(
        default=None,
        description=(
            "추가 분해 축 — 'day'(app_settings.timezone 로컬 날짜) 또는 'policy'. "
            "생략하면 기존 모델별 집계만 나가고 buckets 는 null 이다."
        ),
    ),
    db: Session = Depends(get_db),
) -> UsageResponse:
    """모델·기간별 토큰 합·추정 비용 합·평균 지연 시간·성공/실패 수.

    `group_by` 는 **추가** 파라미터다 (Phase 6). 주면 같은 기간·필터를 일별 또는
    정책별로 한 번 더 분해해 `buckets` 에 싣는다.
    """
    return service.get_usage(
        db,
        range_start=range_start,
        range_end=range_end,
        model=model,
        provider=provider,
        group_by=group_by,
    )
