"""`/api/policies` 와 `/api/query-runs` 라우터 (스켈레톤).

Phase 1 담당 트랙: **정책 API**

계약상 제약:
- 기간·라인 수 상한은 **서버에서** 강제한다. UI 제한은 API 직접 호출로 우회된다.
- 쿼리 실행 경로는 한 곳으로 모은다 (나중에 selector 범위 검사를 끼워 넣을 수 있도록).
- `DELETE /policies/{id}` 는 실제 삭제가 아니라 `active=false` 다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.api import (
    PolicyCreate,
    PolicyPreviewRequest,
    PolicyPreviewResponse,
    PolicyRead,
    PolicyUpdate,
    QueryRunCreateRequest,
    QueryRunRead,
)
from app.stub import not_implemented

TRACK = "정책 API"

router = APIRouter(prefix="/policies", tags=["policies"])
query_runs_router = APIRouter(prefix="/query-runs", tags=["query-runs"])


@router.get("", response_model=list[PolicyRead])
def list_policies(active: bool | None = None, db: Session = Depends(get_db)) -> list[PolicyRead]:
    not_implemented(TRACK)


@router.post("", response_model=PolicyRead, status_code=status.HTTP_201_CREATED)
def create_policy(payload: PolicyCreate, db: Session = Depends(get_db)) -> PolicyRead:
    not_implemented(TRACK)


@router.post("/preview", response_model=PolicyPreviewResponse)
def preview_policy(
    payload: PolicyPreviewRequest, db: Session = Depends(get_db)
) -> PolicyPreviewResponse:
    """저장 전 실행 결과 미리보기. 표시되는 로그는 마스킹을 거친 값이어야 한다."""
    not_implemented(TRACK)


@router.get("/{policy_id}", response_model=PolicyRead)
def get_policy(policy_id: int, db: Session = Depends(get_db)) -> PolicyRead:
    not_implemented(TRACK)


@router.patch("/{policy_id}", response_model=PolicyRead)
def update_policy(
    policy_id: int, payload: PolicyUpdate, db: Session = Depends(get_db)
) -> PolicyRead:
    not_implemented(TRACK)


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_policy(policy_id: int, db: Session = Depends(get_db)) -> None:
    """정책을 지우면 query_runs·분석 이력이 맥락을 잃으므로 비활성화만 한다."""
    not_implemented(TRACK)


@router.post(
    "/{policy_id}/query-runs", response_model=QueryRunRead, status_code=status.HTTP_201_CREATED
)
def create_query_run(
    policy_id: int, payload: QueryRunCreateRequest, db: Session = Depends(get_db)
) -> QueryRunRead:
    """정책 실행 → 로그 조회 → 마스킹 → 정규화 → fingerprint → 오류 그룹 생성.

    처리 순서는 **마스킹 → 정규화 → fingerprint** 로 고정한다.
    """
    not_implemented(TRACK)


@query_runs_router.get("/{run_id}", response_model=QueryRunRead)
def get_query_run(run_id: int, db: Session = Depends(get_db)) -> QueryRunRead:
    not_implemented(TRACK)
