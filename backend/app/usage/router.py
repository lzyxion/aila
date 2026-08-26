"""`/api/usage` 라우터 (스켈레톤).

Phase 1 담당 트랙: **LLM 분석**

계약상 제약: `estimated_cost` 는 계산 시점 단가표 기준 **추정**이다. 사용량 대시보드는
사후 확인일 뿐이므로, 비용 차단은 일일 분석 한도가 담당한다.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.api import UsageResponse
from app.stub import not_implemented

TRACK = "LLM 분석"

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=UsageResponse)
def get_usage(
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    model: str | None = None,
    provider: str | None = None,
    db: Session = Depends(get_db),
) -> UsageResponse:
    not_implemented(TRACK)
