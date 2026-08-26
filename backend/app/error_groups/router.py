"""`/api/query-runs/{id}/error-groups`, `/api/error-groups/{id}` 라우터 (스켈레톤).

Phase 1 담당 트랙: **정책 API**

계약상 제약:
- 목록에는 전체 원문이 아니라 대표 메시지·횟수·최초/최종 발생 시각만 준다.
- `analysis_status` 는 그룹 id 가 아니라 **fingerprint 기준**으로 채운다 —
  이전 조회에서 이미 분석된 그룹이 "미분석"으로 보이면 그대로 중복 과금이 된다.
- 응답에 실리는 로그는 전부 마스킹된 값이다 (원본은 저장하지 않는다).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.api import ErrorGroupDetail, ErrorGroupListResponse
from app.stub import not_implemented

TRACK = "정책 API"

router = APIRouter(tags=["error-groups"])


@router.get("/query-runs/{run_id}/error-groups", response_model=ErrorGroupListResponse)
def list_error_groups(
    run_id: int,
    limit: int = Query(default=50, gt=0, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ErrorGroupListResponse:
    not_implemented(TRACK)


@router.get("/error-groups/{group_id}", response_model=ErrorGroupDetail)
def get_error_group(group_id: int, db: Session = Depends(get_db)) -> ErrorGroupDetail:
    """상세·마스킹된 대표 로그·발생 추이·같은 fingerprint 의 과거 분석 이력."""
    not_implemented(TRACK)
