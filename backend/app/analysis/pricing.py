"""추정 비용 계산.

단가표는 **코드가 아니라 설정 데이터**다 (`app_settings.model_pricing`) — 단가 변경이
배포 없이 반영되어야 하기 때문이다. 형태는 다음과 같다.

```json
{"gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.0006, "currency": "USD"}}
```

>>> 추정 비용은 추정이다 <<<
모델 단가는 예고 없이 바뀌고 캐시 적중·배치 할인 때문에 실제 청구액과 벌어진다.
그래서 계산에 **실제로 쓴 단가를 `pricing_snapshot` 에 복사해** 저장한다. 단가표에
모델이 없으면 값을 지어내지 않고 `estimated_cost=None` 으로 남긴다 — 0 원으로 적으면
"싸다"로 읽히고, 정산 근거로 쓰려 드는 순간 어긋난다.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.models import SETTING_MODEL_PRICING, AppSetting

#: `llm_usage_records.estimated_cost` 는 Numeric(14, 6) 이다.
COST_QUANTUM = Decimal("0.000001")

DEFAULT_CURRENCY = "USD"


def load_model_pricing(db: Session) -> dict[str, Any]:
    """`app_settings.model_pricing` 단가표. 없으면 빈 표."""
    row = db.get(AppSetting, SETTING_MODEL_PRICING)
    value = row.value if row is not None else None
    if isinstance(value, dict):
        return value
    return {}


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def pricing_entry(pricing: dict[str, Any], model: str) -> dict[str, Any] | None:
    """모델 단가 한 줄. 표에 없거나 형태가 깨졌으면 None."""
    entry = pricing.get(model)
    if not isinstance(entry, dict):
        return None
    input_rate = _to_decimal(entry.get("input_per_1k"))
    output_rate = _to_decimal(entry.get("output_per_1k"))
    if input_rate is None and output_rate is None:
        return None
    return {
        "model": model,
        "input_per_1k": entry.get("input_per_1k"),
        "output_per_1k": entry.get("output_per_1k"),
        "currency": entry.get("currency", DEFAULT_CURRENCY),
    }


def estimate_cost(
    entry: dict[str, Any] | None, input_tokens: int, output_tokens: int
) -> Decimal | None:
    """단가표 한 줄로 추정 비용을 계산한다. 단가가 없으면 None (0 이 아니다)."""
    if entry is None:
        return None
    input_rate = _to_decimal(entry.get("input_per_1k")) or Decimal("0")
    output_rate = _to_decimal(entry.get("output_per_1k")) or Decimal("0")
    thousand = Decimal("1000")
    cost = (
        Decimal(int(input_tokens)) / thousand * input_rate
        + Decimal(int(output_tokens)) / thousand * output_rate
    )
    return cost.quantize(COST_QUANTUM)


def price(
    db: Session, model: str, input_tokens: int, output_tokens: int
) -> tuple[Decimal | None, dict[str, Any] | None]:
    """(추정 비용, 사용한 단가 스냅샷). 계산과 스냅샷 복사를 한 곳에서 한다."""
    entry = pricing_entry(load_model_pricing(db), model)
    return estimate_cost(entry, input_tokens, output_tokens), entry


__all__ = [
    "COST_QUANTUM",
    "DEFAULT_CURRENCY",
    "estimate_cost",
    "load_model_pricing",
    "price",
    "pricing_entry",
]
