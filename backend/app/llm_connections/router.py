"""`/api/llm-connections` 라우터 (스켈레톤).

Phase 1 담당 트랙: **LLM 분석**
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.api import (
    ConnectionTestResponse,
    LLMConnectionCreate,
    LLMConnectionRead,
    LLMConnectionTestRequest,
    LLMConnectionUpdate,
)
from app.stub import not_implemented

TRACK = "LLM 분석"

router = APIRouter(prefix="/llm-connections", tags=["llm-connections"])


@router.get("", response_model=list[LLMConnectionRead])
def list_llm_connections(db: Session = Depends(get_db)) -> list[LLMConnectionRead]:
    not_implemented(TRACK)


@router.post("", response_model=LLMConnectionRead, status_code=status.HTTP_201_CREATED)
def create_llm_connection(
    payload: LLMConnectionCreate, db: Session = Depends(get_db)
) -> LLMConnectionRead:
    """api_key 는 암호화 저장하고 응답에는 마스킹된 값만 싣는다.

    `is_default=True` 로 만들면 기존 기본 연결은 해제한다 (기본 연결은 최대 하나).
    """
    not_implemented(TRACK)


@router.post("/test", response_model=ConnectionTestResponse)
def test_llm_connection(
    payload: LLMConnectionTestRequest, db: Session = Depends(get_db)
) -> ConnectionTestResponse:
    """연결 테스트도 실제 과금 호출이다 — 최소 토큰으로 보낸다."""
    not_implemented(TRACK)


@router.get("/{connection_id}", response_model=LLMConnectionRead)
def get_llm_connection(connection_id: int, db: Session = Depends(get_db)) -> LLMConnectionRead:
    not_implemented(TRACK)


@router.patch("/{connection_id}", response_model=LLMConnectionRead)
def update_llm_connection(
    connection_id: int, payload: LLMConnectionUpdate, db: Session = Depends(get_db)
) -> LLMConnectionRead:
    not_implemented(TRACK)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_llm_connection(connection_id: int, db: Session = Depends(get_db)) -> None:
    """분석 이력이 참조하므로 실제 삭제가 아니라 `active=false` 비활성화다."""
    not_implemented(TRACK)
