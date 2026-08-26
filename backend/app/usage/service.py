"""토큰·비용 집계.

Phase 2 담당 트랙: **분석 플로우·usage·보고서**

계약상 제약: `estimated_cost` 는 계산 시점 단가표 기준 **추정**이다. 사용량 대시보드는
사후 확인일 뿐이므로 비용 차단은 일일 분석 한도(`analysis.service`)가 담당한다. 여기서
막을 수 있는 것은 아무것도 없다.

집계는 SQL 이 아니라 Python 에서 한다 — `Numeric` 합계는 백엔드(SQLite/PostgreSQL)마다
반환 타입이 달라지고, 단가표에 없는 모델의 `NULL` 비용을 0 으로 접어버리기 쉽다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import UsageStatus
from app.models import (
    AnalysisJob,
    AnalysisPolicy,
    AnalysisUsageRecord,
    ErrorGroup,
    QueryRun,
)
from app.policies.service import analysis_timezone
from app.schemas.api import UsageAggregate, UsageBucket, UsageResponse

#: 기간을 주지 않았을 때의 기본 조회 범위.
DEFAULT_RANGE_DAYS = 30

#: `group_by` 로 받는 값. 그 외는 422 로 튕긴다 (오타가 조용히 "분해 없음"이 되면
#: 화면은 빈 표를 보여주면서 이유를 말하지 못한다).
GROUP_BY_DAY = "day"
GROUP_BY_POLICY = "policy"
GROUP_BY_VALUES = (GROUP_BY_DAY, GROUP_BY_POLICY)

#: 정책 연결이 끊긴 작업(그룹·조회 이력·정책 중 하나가 지워진 경우)의 버킷 키.
UNKNOWN_POLICY_KEY = "unknown"
UNKNOWN_POLICY_LABEL = "정책 없음"

HTTP_422 = 422


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


def _local_day_key(moment: datetime, tz: tzinfo) -> str:
    """UTC 저장 시각 → 기준 타임존의 로컬 날짜 문자열.

    기준은 서버 로케일이 아니라 `app_settings.timezone` 이다 — 일일 분석 한도가
    쓰는 것과 **같은 규칙**이어야 "한도 5 회 중 3 회 썼다"와 "오늘 3 건"이 화면에서
    맞아떨어진다. UTC 날짜로 묶으면 KST 오전 9 시에 날짜가 바뀌어 어긋난다.
    """
    return _as_utc(moment).astimezone(tz).strftime("%Y-%m-%d")


#: (사용 기록, 정책 id, 정책명) — 정책이 끊긴 행은 뒤 두 값이 None 이다.
PolicyRow = tuple[AnalysisUsageRecord, int | None, str | None]


def _policy_rows(db: Session, conditions: list) -> list[PolicyRow]:
    """usage → job → group → run → policy 를 **한 번의 조인**으로 읽는다 (N+1 금지).

    중간 어느 고리가 끊겨도(그룹·조회 이력·정책 삭제) 사용 기록 자체는 남아야 하므로
    전부 outer join 이다 — 끊긴 행은 `unknown` 버킷으로 간다.
    """
    stmt = (
        select(AnalysisUsageRecord, AnalysisPolicy.id, AnalysisPolicy.name)
        .outerjoin(AnalysisJob, AnalysisJob.id == AnalysisUsageRecord.analysis_job_id)
        .outerjoin(ErrorGroup, ErrorGroup.id == AnalysisJob.error_group_id)
        .outerjoin(QueryRun, QueryRun.id == ErrorGroup.query_run_id)
        .outerjoin(AnalysisPolicy, AnalysisPolicy.id == QueryRun.policy_id)
        .where(*conditions)
    )
    return [(record, policy_id, name) for record, policy_id, name in db.execute(stmt)]


def _to_usage_bucket(key: str, label: str, bucket: _Bucket) -> UsageBucket:
    return UsageBucket(
        key=key,
        label=label,
        input_tokens=bucket.input_tokens,
        output_tokens=bucket.output_tokens,
        # 계산 가능한 기록이 하나도 없으면 None 이다 (0 이 아니다 — `_Bucket` 규칙 그대로).
        estimated_cost=bucket.estimated_cost,
        job_count=bucket.job_count,
        failure_count=bucket.failure_count,
    )


def _day_buckets(db: Session, records: list[AnalysisUsageRecord]) -> list[UsageBucket]:
    tz = analysis_timezone(db)
    grouped: dict[str, _Bucket] = {}
    for record in records:
        grouped.setdefault(_local_day_key(record.created_at, tz), _Bucket()).add(record)
    # 날짜는 시간순이 유일하게 말이 되는 순서다.
    return [_to_usage_bucket(key, key, grouped[key]) for key in sorted(grouped)]


def _policy_buckets(db: Session, conditions: list) -> list[UsageBucket]:
    grouped: dict[str, _Bucket] = {}
    labels: dict[str, str] = {}
    for record, policy_id, policy_name in _policy_rows(db, conditions):
        key = str(policy_id) if policy_id is not None else UNKNOWN_POLICY_KEY
        labels.setdefault(
            key, policy_name if policy_id is not None else UNKNOWN_POLICY_LABEL
        )
        grouped.setdefault(key, _Bucket()).add(record)

    def _order(key: str) -> tuple[int, int, str]:
        # 끊긴 연결은 항상 마지막 — 정책 목록을 읽는 눈이 먼저 걸리지 않게 한다.
        if key == UNKNOWN_POLICY_KEY:
            return (1, 0, key)
        return (0, int(key), key)

    ordered = sorted(grouped, key=_order)
    return [_to_usage_bucket(key, labels[key], grouped[key]) for key in ordered]


def get_usage(
    db: Session,
    *,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    model: str | None = None,
    provider: str | None = None,
    group_by: str | None = None,
) -> UsageResponse:
    """모델·기간별 토큰 합·추정 비용 합·평균 지연·성공/실패 수.

    `group_by` 를 주면 같은 기간·필터를 **일별 또는 정책별**로 한 번 더 분해해
    `buckets` 에 싣는다. 기존 `items`(모델별)는 그대로다 — 추가일 뿐 대체가 아니다.
    """
    start, end = resolve_range(range_start, range_end)

    grouping = (group_by or "").strip() or None
    if grouping is not None and grouping not in GROUP_BY_VALUES:
        raise HTTPException(
            status_code=HTTP_422,
            detail=(
                f"group_by 는 {' 또는 '.join(GROUP_BY_VALUES)} 여야 합니다 "
                f"(받은 값: {group_by!r})."
            ),
        )

    conditions = [
        AnalysisUsageRecord.created_at >= start,
        AnalysisUsageRecord.created_at <= end,
    ]
    if model is not None:
        conditions.append(AnalysisUsageRecord.model == model)
    if provider is not None:
        conditions.append(AnalysisUsageRecord.provider == provider)

    records = list(db.scalars(select(AnalysisUsageRecord).where(*conditions)).all())

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

    if grouping == GROUP_BY_DAY:
        bucket_rows = _day_buckets(db, records)
    elif grouping == GROUP_BY_POLICY:
        bucket_rows = _policy_buckets(db, conditions)
    else:
        bucket_rows = None

    return UsageResponse(
        range_start=start,
        range_end=end,
        items=items,
        buckets=bucket_rows,
        total_jobs=sum(item.job_count for item in items),
        total_input_tokens=sum(item.input_tokens for item in items),
        total_output_tokens=sum(item.output_tokens for item in items),
        total_estimated_cost=sum(costed, start=Decimal("0")) if costed else None,
    )


__all__ = [
    "DEFAULT_RANGE_DAYS",
    "GROUP_BY_DAY",
    "GROUP_BY_POLICY",
    "GROUP_BY_VALUES",
    "UNKNOWN_POLICY_KEY",
    "get_usage",
    "resolve_range",
]
