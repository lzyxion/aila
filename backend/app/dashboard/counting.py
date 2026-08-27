"""대시보드 두 화면(`overview` · `summary`)이 공유하는 집계 조각.

같은 규칙을 두 모듈에 손으로 복사하면 **한쪽만 고쳐진다.** Phase 7 검증에서 실제로
그랬다 — `summary` 는 metric 포인트를 시각별로 접어 실었는데 `overview` 는 접지 않아,
`sum by (service)` 가 돌려준 같은 시각의 점이 서비스 수만큼 차트에 그려졌다.
접기 규칙과 회차 COUNT 규칙을 여기 한 곳에 둔다.

이 모듈은 순수 집계만 한다 — Loki 호출도, 응답 조립도 하지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AnalysisJob, ErrorGroup
from app.schemas.logrecord import CountPoint


def fold_by_timestamp(points: Sequence[CountPoint]) -> list[CountPoint]:
    """같은 시각의 여러 시리즈를 한 점으로 접는다 (라벨은 버린다).

    metric 쿼리는 `sum by (service)` 라 서비스가 셋이면 한 시각에 점이 셋 나온다.
    그대로 실으면 차트가 같은 시각을 세 번 그린다 — 선이 지그재그로 접히고 "선 아래
    면적 = 총 건수" 가 깨진다. 합계가 전 시리즈의 합인 것과 **같은 기준**으로 접어야
    두 숫자가 같은 것을 가리킨다.

    라벨을 버리므로 **서비스별 분해는 접기 전 포인트로 먼저 계산해야 한다.**
    """
    totals: dict[datetime, float] = {}
    for point in points:
        totals[point.timestamp] = totals.get(point.timestamp, 0.0) + point.value
    return [
        CountPoint(timestamp=timestamp, value=totals[timestamp])
        for timestamp in sorted(totals)
    ]


def group_count(db: Session, run_id: int) -> int:
    """조회 회차의 **전체** 오류 그룹 수 (DB COUNT).

    상위 N 목록(`top_groups`) 길이와 다르다 — 목록 길이를 지표 자리에 쓰면 그룹이
    50 개여도 화면에는 영원히 "10" 이 뜬다.
    """
    return int(
        db.scalar(select(func.count(ErrorGroup.id)).where(ErrorGroup.query_run_id == run_id))
        or 0
    )


def unanalyzed_group_count(db: Session, run_id: int) -> int:
    """`run_id` 의 그룹 중 **fingerprint 분석 이력이 전혀 없는** 그룹 수.

    판정 기준이 그룹 id 가 아니라 fingerprint 인 것이 핵심이다 — 그룹 id 는 조회
    회차마다 새로 생기므로, id 로 세면 어제 분석한 오류가 오늘도 "미분석" 으로 잡혀
    자동 분석이 매 회차 같은 오류를 다시 태운다.

    상태도 보지 않는다. 실패한 분석도 "이력이 있다" 로 친다 — 실패를 미분석으로 세면
    같은 실패를 자동 분석이 무한히 재시도한다.
    """
    exists = (
        select(AnalysisJob.id).where(AnalysisJob.fingerprint == ErrorGroup.fingerprint).exists()
    )
    return int(
        db.scalar(
            select(func.count(ErrorGroup.id)).where(ErrorGroup.query_run_id == run_id, ~exists)
        )
        or 0
    )


__all__ = ["fold_by_timestamp", "group_count", "unanalyzed_group_count"]
