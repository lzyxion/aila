"""`/api/loki-connections` 라우터 (스켈레톤).

Phase 1 담당 트랙: **Loki 어댑터**
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.api import (
    ConnectionTestResponse,
    LabelValuesResponse,
    LokiConnectionCreate,
    LokiConnectionRead,
    LokiConnectionTestRequest,
    LokiConnectionUpdate,
)
from app.stub import not_implemented

TRACK = "Loki 어댑터"

router = APIRouter(prefix="/loki-connections", tags=["loki-connections"])


@router.get("", response_model=list[LokiConnectionRead])
def list_loki_connections(db: Session = Depends(get_db)) -> list[LokiConnectionRead]:
    not_implemented(TRACK)


@router.post("", response_model=LokiConnectionRead, status_code=status.HTTP_201_CREATED)
def create_loki_connection(
    payload: LokiConnectionCreate, db: Session = Depends(get_db)
) -> LokiConnectionRead:
    """secret 은 `app.crypto.encrypt()` 로 암호화해 저장한다. 평문 저장 금지."""
    not_implemented(TRACK)


@router.post("/test", response_model=ConnectionTestResponse)
def test_loki_connection(
    payload: LokiConnectionTestRequest, db: Session = Depends(get_db)
) -> ConnectionTestResponse:
    """저장된 연결 또는 미저장 입력값으로 연결·인증을 테스트한다."""
    not_implemented(TRACK)


@router.get("/{connection_id}", response_model=LokiConnectionRead)
def get_loki_connection(connection_id: int, db: Session = Depends(get_db)) -> LokiConnectionRead:
    not_implemented(TRACK)


@router.patch("/{connection_id}", response_model=LokiConnectionRead)
def update_loki_connection(
    connection_id: int, payload: LokiConnectionUpdate, db: Session = Depends(get_db)
) -> LokiConnectionRead:
    not_implemented(TRACK)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_loki_connection(connection_id: int, db: Session = Depends(get_db)) -> None:
    """정책이 참조 중일 수 있으므로 실제 삭제가 아니라 `active=false` 비활성화다."""
    not_implemented(TRACK)


@router.get("/{connection_id}/labels", response_model=LabelValuesResponse)
def list_labels(connection_id: int, db: Session = Depends(get_db)) -> LabelValuesResponse:
    """정책 작성 UI 용 라벨 탐색. `supports_label_discovery=False` 면 빈 결과를 준다."""
    not_implemented(TRACK)
