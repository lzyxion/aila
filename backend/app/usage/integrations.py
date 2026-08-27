"""usage 트랙이 **분석 트랙의 계약**을 쓰는 유일한 통로 (지연 import).

Phase 7 담당 트랙: **usage — 일일 한도 게이지**

`GET /usage/daily-limit` 게이지는 429 를 내는 한도 검사와 **같은 숫자**를 보여야 한다.
다르면 게이지를 믿을 수 없고(“아직 2 회 남았다”고 적힌 화면에서 429 가 난다), 그 순간
게이지는 없느니만 못한 화면이 된다. 그래서 사용량 계산도 하루 경계도 여기서 복제하지
않고 `app.analysis.service` 의 것을 그대로 부른다.

지연 import 인 이유는 다른 `*/integrations.py` 와 같다.

1. 트랙 간 결합을 모듈 최상단이 아니라 **호출 시점**으로 미룬다 (import 순환·부재 방어).
2. 테스트의 **단일 mock 지점**이 된다 —
   `patch("app.usage.integrations.daily_usage")` 로 분석 트랙을 건드리지 않고
   게이지 쪽 조립만 검증할 수 있다.

호출하는 쪽은 반드시 `integrations.daily_usage(...)` 처럼 **모듈 속성으로** 접근한다.
`from ... import daily_usage` 로 이름을 미리 묶으면 patch 가 먹지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 타입 체크 전용
    from sqlalchemy.orm import Session


def daily_usage(db: Session, policy: Any | None = None) -> tuple[int, int]:
    """`app.analysis.service.daily_usage` — (오늘 전역 분석 수, 오늘 이 정책의 분석 수).

    한도 검사(`_enforce_daily_limits`)가 429 를 낼 때 세는 것과 **같은 함수**다.
    게이지 전용 집계를 따로 만들지 않는 이유가 이것이다.
    """
    from app.analysis.service import daily_usage as _daily_usage

    return _daily_usage(db, policy)


def analysis_day_start(tz: tzinfo) -> datetime:
    """한도가 쓰는 "오늘 00:00" 을 **UTC** 로 (= `_start_of_day(_now(), tz)`).

    게이지의 `date` 는 이 경계에서 되돌려 만든다. 지금 시각을 따로 읽어 날짜를 찍으면
    자정 전후 한 틱 차이로 "날짜는 오늘인데 사용량은 어제 것" 같은 조합이 나온다.

    분석 트랙의 내부 헬퍼(`_now`·`_start_of_day`)를 쓰는 것은 의도다 — 경계 계산은
    한 곳에만 있어야 하고, 그 한 곳이 한도 검사가 쓰는 바로 그 코드여야 한다.
    이름이 사라지면(트랙이 리팩터링하면) 같은 규칙을 여기서 다시 계산해 내려간다.
    """
    from app.analysis import service as analysis_service

    now = getattr(analysis_service, "_now", None)
    start_of_day = getattr(analysis_service, "_start_of_day", None)
    if now is None or start_of_day is None:  # pragma: no cover - 트랙 리팩터링 방어
        local = datetime.now(UTC).astimezone(tz)
        return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    return start_of_day(now(), tz)


__all__ = ["analysis_day_start", "daily_usage"]
