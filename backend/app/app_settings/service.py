"""`app_settings` 읽기·쓰기와 **값 형식 검증**.

쓰기 경로가 하나뿐이어야 검증도 한 곳에 모인다. 그래서 라우터는 HTTP 경계만 담당하고,
"어떤 키를 쓸 수 있는가"와 "그 값이 말이 되는가"는 전부 여기서 판정한다.

>>> 왜 화이트리스트인가 <<<
`app_settings` 는 키-값 테이블이라 아무 키나 만들 수 있다. 열어 두면 오타(`daily_limit`)
가 조용히 새 행으로 저장되고, 읽는 쪽은 기본값을 계속 쓰면서 화면에는 "설정했다"고
보인다 — 비용 한도에서 이런 실패는 요금으로 돌아온다. 예약 3 종만 받는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    SETTING_DAILY_ANALYSIS_LIMIT,
    SETTING_MODEL_PRICING,
    SETTING_SAMPLE_RETENTION_DAYS,
    AppSetting,
)
from app.schemas.api import AppSettingListResponse, AppSettingRead

HTTP_422 = 422

#: 단가표 한 줄에서 숫자로 받아야 하는 필드.
PRICING_RATE_FIELDS = ("input_per_1k", "output_per_1k")

DESCRIPTIONS: dict[str, str] = {
    SETTING_DAILY_ANALYSIS_LIMIT: (
        "전역 일일 분석 횟수 상한 (UTC 자정 기준). 0 이면 분석을 시작할 수 없다."
    ),
    SETTING_SAMPLE_RETENTION_DAYS: (
        "error_samples 보존 일수. 지난 샘플은 삭제한다. 0 이면 자동 삭제를 끈다."
    ),
    SETTING_MODEL_PRICING: (
        "모델 단가표 {model: {input_per_1k, output_per_1k, currency}}. "
        "표에 없는 모델의 추정 비용은 0 이 아니라 None 으로 남는다."
    ),
}


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=HTTP_422, detail=message)


def _not_found(key: str) -> HTTPException:
    allowed = ", ".join(sorted(DESCRIPTIONS))
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"'{key}' 는 설정 키가 아닙니다. 사용할 수 있는 키: {allowed}.",
    )


# ------------------------------------------------------------------- 검증


def _validate_non_negative_int(key: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _unprocessable(f"{key} 는 0 이상의 정수여야 합니다 (받은 값: {value!r}).")
    if value < 0:
        raise _unprocessable(f"{key} 는 0 이상이어야 합니다 (받은 값: {value}).")
    return value


def _validate_model_pricing(value: Any) -> dict[str, Any]:
    """`{model: {input_per_1k, output_per_1k, currency}}` 인지 확인한다.

    단가를 지어내지 않는 것이 원칙이므로, 형태가 깨진 표는 **저장 자체를 막는다** —
    저장해 두고 계산 시점에 무시하면 화면에는 단가가 있는데 비용은 계속 비어 있는,
    가장 설명하기 어려운 상태가 된다.
    """
    if not isinstance(value, dict):
        raise _unprocessable("model_pricing 은 객체여야 합니다 (예: {\"gpt-4o-mini\": {...}}).")
    for model, entry in value.items():
        if not isinstance(model, str) or not model.strip():
            raise _unprocessable("model_pricing 의 키는 비어 있지 않은 모델명이어야 합니다.")
        if not isinstance(entry, dict):
            raise _unprocessable(f"model_pricing['{model}'] 은 객체여야 합니다.")
        if not any(field in entry for field in PRICING_RATE_FIELDS):
            raise _unprocessable(
                f"model_pricing['{model}'] 에 input_per_1k 또는 output_per_1k 가 있어야 합니다."
            )
        for field in PRICING_RATE_FIELDS:
            rate = entry.get(field)
            if rate is None:
                continue
            if isinstance(rate, bool) or not isinstance(rate, (int, float)):
                raise _unprocessable(
                    f"model_pricing['{model}'].{field} 는 숫자여야 합니다 (받은 값: {rate!r})."
                )
            if rate < 0:
                raise _unprocessable(f"model_pricing['{model}'].{field} 는 0 이상이어야 합니다.")
        currency = entry.get("currency")
        if currency is not None and not isinstance(currency, str):
            raise _unprocessable(f"model_pricing['{model}'].currency 는 문자열이어야 합니다.")
    return value


def validate_value(key: str, value: Any) -> Any:
    """예약 키별 값 형식 검증. 통과하면 저장할 값을 그대로 돌려준다."""
    if key not in DESCRIPTIONS:
        raise _not_found(key)
    if value is None:
        # None 은 "행을 두되 서버 기본값을 쓴다"는 뜻이다 (행 삭제 대신).
        return None
    if key in (SETTING_DAILY_ANALYSIS_LIMIT, SETTING_SAMPLE_RETENTION_DAYS):
        return _validate_non_negative_int(key, value)
    return _validate_model_pricing(value)


# ------------------------------------------------------------------- 조회


def default_value(key: str) -> Any:
    """행이 없을 때 실제로 적용되는 값 (`app.config.Settings` 기본값)."""
    settings = get_settings()
    if key == SETTING_DAILY_ANALYSIS_LIMIT:
        return settings.default_daily_analysis_limit
    if key == SETTING_SAMPLE_RETENTION_DAYS:
        return settings.default_sample_retention_days
    return {}


def _read(key: str, row: AppSetting | None) -> AppSettingRead:
    value = row.value if row is not None else None
    return AppSettingRead(
        key=key,
        value=value,
        description=(row.description if row is not None else None) or DESCRIPTIONS[key],
        updated_at=row.updated_at if row is not None else None,
        effective_value=value if value is not None else default_value(key),
    )


def list_settings(db: Session) -> AppSettingListResponse:
    """예약 3 종만 돌려준다 (행이 없어도 기본값과 함께 자리를 만든다)."""
    rows = {
        row.key: row
        for row in db.scalars(
            select(AppSetting).where(AppSetting.key.in_(sorted(DESCRIPTIONS)))
        ).all()
    }
    return AppSettingListResponse(
        items=[_read(key, rows.get(key)) for key in sorted(DESCRIPTIONS)]
    )


def get_setting(db: Session, key: str) -> AppSettingRead:
    if key not in DESCRIPTIONS:
        raise _not_found(key)
    return _read(key, db.get(AppSetting, key))


def update_setting(db: Session, key: str, value: Any) -> AppSettingRead:
    """예약 키 하나를 갱신한다 (없으면 만든다)."""
    validated = validate_value(key, value)

    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, description=DESCRIPTIONS[key])
        db.add(row)
    row.value = validated
    if not row.description:
        row.description = DESCRIPTIONS[key]
    # SQLite 는 onupdate 가 값이 안 바뀐 UPDATE 를 건너뛸 수 있어 명시적으로 찍는다.
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return _read(key, row)


__all__ = [
    "DESCRIPTIONS",
    "default_value",
    "get_setting",
    "list_settings",
    "update_setting",
    "validate_value",
]
