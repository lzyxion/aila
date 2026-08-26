"""`/api/policies` 와 `/api/query-runs` 라우터.

Phase 1 담당 트랙: **정책 API**

계약상 제약:
- 기간·라인 수 상한은 **서버에서** 강제한다. UI 제한은 API 직접 호출로 우회된다.
- 쿼리 실행 경로는 한 곳으로 모은다 (나중에 selector 범위 검사를 끼워 넣을 수 있도록).
- `DELETE /policies/{id}` 는 실제 삭제가 아니라 `active=false` 다.

로직은 전부 `app.policies.service` 에 있다. 라우터는 HTTP 경계만 담당한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.policies import service
from app.schemas.api import (
    PolicyCreate,
    PolicyPreviewRequest,
    PolicyPreviewResponse,
    PolicyRead,
    PolicyUpdate,
    QueryRunCreateRequest,
    QueryRunRead,
)

TRACK = "정책 API"

router = APIRouter(prefix="/policies", tags=["policies"])
query_runs_router = APIRouter(prefix="/query-runs", tags=["query-runs"])


@router.get("", response_model=list[PolicyRead])
def list_policies(active: bool | None = None, db: Session = Depends(get_db)) -> list[PolicyRead]:
    return service.list_policies(db, active)


@router.post("", response_model=PolicyRead, status_code=status.HTTP_201_CREATED)
def create_policy(payload: PolicyCreate, db: Session = Depends(get_db)) -> PolicyRead:
    return service.create_policy(db, payload)


@router.post("/preview", response_model=PolicyPreviewResponse)
def preview_policy(
    payload: PolicyPreviewRequest, db: Session = Depends(get_db)
) -> PolicyPreviewResponse:
    """저장 전 실행 결과 미리보기. 표시되는 로그는 마스킹을 거친 값이어야 한다."""
    return service.preview_policy(db, payload)


@router.get("/{policy_id}", response_model=PolicyRead)
def get_policy(policy_id: int, db: Session = Depends(get_db)) -> PolicyRead:
    return service.get_policy(db, policy_id)


@router.patch("/{policy_id}", response_model=PolicyRead)
def update_policy(
    policy_id: int, payload: PolicyUpdate, db: Session = Depends(get_db)
) -> PolicyRead:
    return service.update_policy(db, policy_id, payload)


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_policy(policy_id: int, db: Session = Depends(get_db)) -> None:
    """정책을 지우면 query_runs·분석 이력이 맥락을 잃으므로 비활성화만 한다."""
    service.deactivate_policy(db, policy_id)


@router.post(
    "/{policy_id}/query-runs", response_model=QueryRunRead, status_code=status.HTTP_201_CREATED
)
def create_query_run(
    policy_id: int, payload: QueryRunCreateRequest, db: Session = Depends(get_db)
) -> QueryRunRead:
    """정책 실행 → 로그 조회 → 마스킹 → 정규화 → fingerprint → 오류 그룹 생성.

    처리 순서는 **마스킹 → 정규화 → fingerprint** 로 고정한다.

    요청 기간·라인 수가 정책 한도를 넘으면 422 로 거절하지 않고 **한도로 clamp** 하며,
    조정 사실은 응답 `warnings` 에 `range_clamped` / `limit_clamped` 코드로 남긴다.
    조회가 실패해도 201 로 `status="failed"` 인 조회 이력을 돌려준다.
    """
    return service.create_query_run(db, policy_id, payload)


@query_runs_router.get("/{run_id}", response_model=QueryRunRead)
def get_query_run(run_id: int, db: Session = Depends(get_db)) -> QueryRunRead:
    return service.get_query_run(db, run_id)
