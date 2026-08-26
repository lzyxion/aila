"""토큰·비용 집계.

Phase 2 담당 트랙: **분석 플로우·usage·보고서**

계약상 제약: `estimated_cost` 는 계산 시점 단가표 기준 **추정**이다. 사용량 대시보드는
사후 확인일 뿐이므로 비용 차단은 일일 분석 한도(`analysis.service`)가 담당한다. 여기서
막을 수 있는 것은 아무것도 없다.

집계는 SQL 이 아니라 Python 에서 한다 — `Numeric` 합계는 백엔드(SQLite/PostgreSQL)마다
반환 타입이 달라지고, 단가표에 없는 모델의 `NULL` 비용을 0 으로 접어버리기 쉽다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import UsageStatus
from app.models import AnalysisUsageRecord
from app.schemas.api import UsageAggregate, UsageResponse

#: 기간을 주지 않았을 때의 기본 조회 범위.
DEFAULT_RANGE_DAYS = 30


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def resolve_range(
    range_start: datetime | None, range_end: datetime | None
) -> tuple[datetime, datetime]:
    end = _as_utc(range_end) if range_end is not None else _now()
    start = (
        _as_utc(range_start)
        if range_start is not None
        else end - timedelta(days=DEFAULT_RANGE_DAYS)
    )
    return start, end


class _Bucket:
    """(provider, model) 하나의 누적치."""

    def __init__(self) -> None:
        self.job_count = 0
        self.failure_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_total = Decimal("0")
        self.cost_count = 0
        self.latency_total = 0
        self.latency_count = 0

    def add(self, record: AnalysisUsageRecord) -> None:
        self.job_count += 1
        if record.status == UsageStatus.FAILED.value:
            self.failure_count += 1
        self.input_tokens += record.input_tokens or 0
        self.output_tokens += record.output_tokens or 0
        if record.estimated_cost is not None:
            # 단가표에 없는 모델은 None 이다 — 0 으로 접으면 "쌌다"로 읽힌다.
            self.cost_total += Decimal(str(record.estimated_cost))
            self.cost_count += 1
        if record.latency_ms is not None:
            self.latency_total += record.latency_ms
            self.latency_count += 1

    @property
    def estimated_cost(self) -> Decimal | None:
        """비용을 계산할 수 있는 기록이 하나도 없으면 None (0 이 아니다)."""
        return self.cost_total if self.cost_count else None

    @property
    def avg_latency_ms(self) -> float | None:
        if self.latency_count == 0:
            return None
        return round(self.latency_total / self.latency_count, 1)


def get_usage(
    db: Session,
    *,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    model: str | None = None,
    provider: str | None = None,
) -> UsageResponse:
    """모델·기간별 토큰 합·추정 비용 합·평균 지연·성공/실패 수."""
    start, end = resolve_range(range_start, range_end)

    conditions = [
        AnalysisUsageRecord.created_at >= start,
        AnalysisUsageRecord.created_at <= end,
    ]
    if model is not None:
        conditions.append(AnalysisUsageRecord.model == model)
    if provider is not None:
        conditions.append(AnalysisUsageRecord.provider == provider)

    records = db.scalars(select(AnalysisUsageRecord).where(*conditions)).all()

    buckets: dict[tuple[str, str], _Bucket] = {}
    for record in records:
        buckets.setdefault((record.provider, record.model), _Bucket()).add(record)

    items = [
        UsageAggregate(
            provider=key[0],
            model=key[1],
            job_count=bucket.job_count,
            failure_count=bucket.failure_count,
            input_tokens=bucket.input_tokens,
            output_tokens=bucket.output_tokens,
            estimated_cost=bucket.estimated_cost,
            avg_latency_ms=bucket.avg_latency_ms,
        )
        for key, bucket in sorted(buckets.items())
    ]

    # 계산 가능한 항목이 하나도 없으면 합계도 None 이다 — 0 으로 적으면 "이번 달은
    # 공짜였다"로 읽히는데, 실제로는 단가표가 비어 있어 계산을 못 한 것뿐이다.
    costed = [item.estimated_cost for item in items if item.estimated_cost is not None]

    return UsageResponse(
        range_start=start,
        range_end=end,
        items=items,
        total_jobs=sum(item.job_count for item in items),
        total_input_tokens=sum(item.input_tokens for item in items),
        total_output_tokens=sum(item.output_tokens for item in items),
        total_estimated_cost=sum(costed, start=Decimal("0")) if costed else None,
    )


__all__ = ["DEFAULT_RANGE_DAYS", "get_usage", "resolve_range"]
