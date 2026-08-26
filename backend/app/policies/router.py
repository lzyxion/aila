"""`/api/policies` 와 `/api/query-runs` 라우터.

Phase 1 담당 트랙: **정책 API**

계약상 제약:
- 기간·라인 수 상한은 **서버에서** 강제한다. UI 제한은 API 직접 호출로 우회된다.
- 쿼리 실행 경로는 한 곳으로 모은다 (나중에 selector 범위 검사를 끼워 넣을 수 있도록).
- `DELETE /policies/{id}` 는 실제 삭제가 아니라 `active=false` 다.

로직은 전부 `app.policies.service` 에 있다. 라우터는 HTTP 경계만 담당한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
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
    QueryRunListResponse,
    QueryRunRead,
    SamplePurgeResponse,
)

TRACK = "정책 API"

router = APIRouter(prefix="/policies", tags=["policies"])
query_runs_router = APIRouter(prefix="/query-runs", tags=["query-runs"])
maintenance_router = APIRouter(prefix="/maintenance", tags=["maintenance"])


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


@router.get("/{policy_id}/query-runs", response_model=QueryRunListResponse)
def list_query_runs(
    policy_id: int,
    limit: int = Query(default=20, gt=0, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> QueryRunListResponse:
    """정책의 실행 이력을 **최신순**으로 준다 (`group_count` 포함).

    실행 직후에만 보이던 조회 결과로 다시 들어갈 수 있어야 한다는 1 차 피드백의
    백엔드 몫이다. 실패한 실행(`status="failed"`)도 그대로 실린다 — 이력에서
    빼면 "왜 결과가 없는지" 를 화면에서 알 방법이 사라진다.
    """
    return service.list_query_runs(db, policy_id, limit=limit, offset=offset)


@query_runs_router.get("/{run_id}", response_model=QueryRunRead)
def get_query_run(run_id: int, db: Session = Depends(get_db)) -> QueryRunRead:
    return service.get_query_run(db, run_id)


@maintenance_router.post("/purge-samples", response_model=SamplePurgeResponse)
def purge_samples(db: Session = Depends(get_db)) -> SamplePurgeResponse:
    """보존 기간(`app_settings.sample_retention_days`)이 지난 대표 로그를 삭제한다.

    자동 실행은 정책 실행 진입점에서 하루 1 회 돈다. 이 엔드포인트는 보존 기간을 방금
    줄였을 때처럼 **지금 당장** 정리해야 하는 경우를 위한 수동 트리거이며, 주기와
    무관하게 즉시 실행한다.

    마스킹 규칙을 강화해도 이미 저장된 샘플에는 소급되지 않는다 — 그래서 삭제가
    유일한 회수 수단이다.
    """
    return service.purge_expired_samples(db)
