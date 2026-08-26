"""그룹화 진입점 — `LogRecord` 목록을 `GroupedError` 목록으로 바꾼다.

트랙 간 계약:
- `NORMALIZATION_RULE_VERSION: str = "v1"`
- `GroupedSample` / `GroupedError` (Pydantic)
- `def group_records(records, *, max_samples_per_group, extra_mask_patterns=()) -> list[GroupedError]`

파이프라인은 레코드 하나당 다음 순서로 돈다. **순서가 계약이다.**

    1. 마스킹      mask(message) · mask(label values)
    2. 파싱        JSON / logfmt / 비정형 구분, 메시지·예외 타입·스택 추출
    3. 정규화      UUID·ID·숫자·시각·IP 를 플레이스홀더로
    4. fingerprint sha256(service + error_type + normalized_message + top_stack_frame)
    5. 집계        같은 fingerprint 를 한 그룹으로

DB·네트워크를 보지 않는 순수 함수다. 입력은 `LogRecord` 목록뿐이고, 같은 입력이면
언제나 같은 출력이 나온다.

반환된 `GroupedSample.masked_log` 는 **이미 마스킹된** 값이다. 호출 측은 이 값을 그대로
`error_samples.masked_log` 에 저장하면 되고, 마스킹 전 원문은 이 함수 밖으로 나가지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.grouping.fingerprint import compute_fingerprint
from app.grouping.normalize import normalize
from app.grouping.parsers import parse_line
from app.masking.service import MASKING_RULE_VERSION, mask, mask_mapping
from app.schemas.logrecord import LogRecord

#: 정규화 규칙 버전. 규칙을 고치면 반드시 올린다 (`error_groups.normalization_rule_version`).
#:
#: - v1: 최초 규칙 집합.
#: - v2: Python 트레이스백의 상위 스택 프레임을 **마지막** `File` 줄(오류 지점)에서
#:   뽑는다. v1 은 첫 줄(가장 바깥 호출자)을 썼는데, Python 은 바깥 -> 안쪽 순으로
#:   출력하므로 진입점만 잡혀 서로 다른 오류가 한 그룹으로 뭉쳤다.
NORMALIZATION_RULE_VERSION: str = "v2"


class GroupedSample(BaseModel):
    """그룹의 대표 로그 샘플. `masked_log` 는 **마스킹이 끝난** 값이다."""

    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime
    masked_log: str
    labels: dict[str, str] = Field(default_factory=dict)
    stacktrace: str | None = None
    masking_rule_version: str = MASKING_RULE_VERSION


class GroupedError(BaseModel):
    """같은 fingerprint 로 묶인 오류 그룹 (`error_groups` 한 행에 대응)."""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    service: str | None = None
    environment: str | None = None
    error_type: str | None = None
    normalized_message: str
    count: int
    first_seen: datetime
    last_seen: datetime
    labels: dict[str, str] = Field(default_factory=dict)
    top_stack_frame: str | None = None
    samples: list[GroupedSample] = Field(default_factory=list)


# ------------------------------------------------------------------ 내부 집계


def _as_utc(value: datetime) -> datetime:
    """naive 시각이 섞여도 비교가 깨지지 않게 UTC 로 맞춘다."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@dataclass
class _Bucket:
    fingerprint: str
    service: str | None
    error_type: str | None
    normalized_message: str
    top_stack_frame: str | None
    first_seen: datetime
    last_seen: datetime
    labels: dict[str, str]
    environments: set[str | None] = field(default_factory=set)
    count: int = 0
    samples: list[GroupedSample] = field(default_factory=list)

    def to_grouped_error(self) -> GroupedError:
        environments = {value for value in self.environments if value}
        return GroupedError(
            fingerprint=self.fingerprint,
            service=self.service,
            environment=environments.pop() if len(environments) == 1 else None,
            error_type=self.error_type,
            normalized_message=self.normalized_message,
            count=self.count,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
            labels=self.labels,
            top_stack_frame=self.top_stack_frame,
            samples=self.samples,
        )


def group_records(
    records: list[LogRecord],
    *,
    max_samples_per_group: int,
    extra_mask_patterns: Sequence[str] = (),
) -> list[GroupedError]:
    """정규화 레코드를 오류 그룹으로 묶는다.

    Args:
        records: 로그 소스 어댑터가 돌려준 정규화 레코드 목록.
        max_samples_per_group: 그룹당 보관할 대표 샘플 수 (정책의 동명 필드). 1 이상.
        extra_mask_patterns: 조직별 사용자 정의 마스킹 정규식.

    Returns:
        발생 건수 내림차순 → 마지막 발생 시각 내림차순 → fingerprint 순의 그룹 목록.

    Raises:
        ValueError: `max_samples_per_group` 이 1 미만일 때.
        app.masking.service.MaskingPatternError: 사용자 정의 정규식이 잘못됐을 때.
    """
    if max_samples_per_group < 1:
        raise ValueError("max_samples_per_group 은 1 이상이어야 합니다.")

    buckets: dict[str, _Bucket] = {}

    for record in records:
        # 1. 마스킹 — 본문과 라벨 값 모두. 이 시점 이후 원문은 어디에도 남기지 않는다.
        masked_log = mask(record.message, extra_mask_patterns)
        masked_labels = mask_mapping(record.labels, extra_mask_patterns)
        service = mask(record.service, extra_mask_patterns) if record.service else None
        environment = (
            mask(record.environment, extra_mask_patterns) if record.environment else None
        )

        # 2. 파싱 — 형식 구분, 메시지·예외 타입·스택트레이스 추출.
        parsed = parse_line(masked_log)

        # 3. 정규화 — 가변값 제거.
        normalized_message = normalize(parsed.message)
        normalized_frame = normalize(parsed.top_stack_frame) or None

        # 4. fingerprint — 상위 스택 프레임만 넣는다 (스택 전체 금지).
        fingerprint = compute_fingerprint(
            service, parsed.error_type, normalized_message, normalized_frame
        )

        occurred_at = _as_utc(record.timestamp)

        # 5. 집계.
        bucket = buckets.get(fingerprint)
        if bucket is None:
            bucket = _Bucket(
                fingerprint=fingerprint,
                service=service,
                error_type=parsed.error_type,
                normalized_message=normalized_message,
                top_stack_frame=normalized_frame,
                first_seen=occurred_at,
                last_seen=occurred_at,
                labels=dict(masked_labels),
            )
            buckets[fingerprint] = bucket
        else:
            bucket.first_seen = min(bucket.first_seen, occurred_at)
            bucket.last_seen = max(bucket.last_seen, occurred_at)
            # 대표 라벨은 그룹 전체가 공유하는 라벨만 남긴다 —
            # 이 값으로 Loki 를 다시 조회해야 하므로 그룹의 모든 레코드에 맞아야 한다.
            bucket.labels = {
                key: value
                for key, value in bucket.labels.items()
                if masked_labels.get(key) == value
            }

        bucket.count += 1
        bucket.environments.add(environment)

        if len(bucket.samples) < max_samples_per_group:
            bucket.samples.append(
                GroupedSample(
                    occurred_at=occurred_at,
                    masked_log=masked_log,
                    labels=masked_labels,
                    stacktrace=parsed.stacktrace,
                    masking_rule_version=MASKING_RULE_VERSION,
                )
            )

    return sorted(
        (bucket.to_grouped_error() for bucket in buckets.values()),
        key=lambda group: (-group.count, -group.last_seen.timestamp(), group.fingerprint),
    )


__all__ = [
    "NORMALIZATION_RULE_VERSION",
    "GroupedError",
    "GroupedSample",
    "group_records",
]
