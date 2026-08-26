"""`/api/settings` 라우터.

계약상 제약:
- 예약 3 종(`daily_analysis_limit`, `model_pricing`, `sample_retention_days`)만 쓸 수 있다.
- 값 형식 검증은 서비스 계층 한 곳에서만 한다 (라우터는 HTTP 경계만).
- 일일 분석 한도는 **비용 차단 장치**다. 여기서 바꾼 값이 곧바로 `POST /analysis-jobs`
  의 429 판정에 쓰인다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.app_settings import service
from app.db import get_db
from app.schemas.api import AppSettingListResponse, AppSettingRead, AppSettingUpdate

TRACK = "정책 API"

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=AppSettingListResponse)
def list_settings(db: Session = Depends(get_db)) -> AppSettingListResponse:
    """예약 설정 전체. 행이 없는 키는 `value=null` + `effective_value=기본값` 으로 온다."""
    return service.list_settings(db)


@router.get("/{key}", response_model=AppSettingRead)
def get_setting(key: str, db: Session = Depends(get_db)) -> AppSettingRead:
    return service.get_setting(db, key)


@router.put("/{key}", response_model=AppSettingRead)
def update_setting(
    key: str, payload: AppSettingUpdate, db: Session = Depends(get_db)
) -> AppSettingRead:
    """예약 키 하나를 갱신한다. 화이트리스트 밖 키는 404, 형식이 틀리면 422."""
    return service.update_setting(db, key, payload.value)
