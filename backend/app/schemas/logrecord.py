"""정규화 로그 레코드 — 로그 소스 추상화의 실질 계약.

`LogSourceProvider.fetch_logs()` 는 반드시 `FetchResult` 를 돌려주고,
`grouping` 이하 모듈은 **이 타입만** 본다. 소스별 라벨 이름 차이는 연결 설정의
`label_mapping` 으로 어댑터가 흡수하며, fingerprint 는 이 정규화 레코드에서
계산되므로 소스가 바뀌어도 값이 흔들리지 않는다.

소스 고유 지식(`| json` 파싱 실패, Loki 5,000 줄 한도, `__error__` 라벨)은
어댑터 안에 가두고, 밖으로는 `fetched` / `dropped` / `warnings` 로만 올린다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LogRecord(BaseModel):
    """소스 무관 정규화 로그 레코드.

    `message` 는 로그 라인 본문이다. 이 시점에는 **아직 마스킹되지 않았을 수 있다** —
    마스킹은 `masking` 모듈이 `masking → 정규화 → fingerprint` 순서로 적용한다.
    """

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(description="로그 발생 시각 (timezone-aware UTC 권장)")
    message: str = Field(description="로그 라인 본문")
    labels: dict[str, str] = Field(default_factory=dict, description="소스 라벨 전체")

    # --- 표준 필드 (label_mapping 을 거쳐 어댑터가 채운다. 없으면 None) ---
    service: str | None = None
    environment: str | None = None
    level: str | None = None


class FetchWarning(BaseModel):
    """어댑터가 표준화해 올리는 경고. 소스 고유 원인을 코드로 구분할 수 있게 한다."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        description="예: parse_error, limit_reached, partial_range, entry_out_of_order"
    )
    message: str
    count: int | None = Field(default=None, description="해당 경고가 몇 건에 해당하는지")


class FetchResult(BaseModel):
    """`fetch_logs()` 반환 타입 — 레코드 + 메타데이터."""

    model_config = ConfigDict(extra="forbid")

    records: list[LogRecord] = Field(default_factory=list)
    fetched: int = Field(default=0, description="소스에서 실제로 읽어온 라인 수")
    dropped: int = Field(
        default=0, description="파싱 실패·필터·한도 절단 등으로 records 에 담기지 못한 라인 수"
    )
    warnings: list[FetchWarning] = Field(default_factory=list)
    #: 소스 한도 또는 정책 한도에 걸려 결과가 잘렸는지. True 면 집계에 쓰면 안 된다.
    truncated: bool = False

    @model_validator(mode="after")
    def _default_fetched(self) -> FetchResult:
        if self.fetched == 0 and self.records:
            self.fetched = len(self.records)
        return self


class TimeRange(BaseModel):
    """조회 구간. 상한 검증(`max_query_range_minutes`)은 어댑터 밖에서 한다."""

    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _check_order(self) -> TimeRange:
        if self.end <= self.start:
            raise ValueError("end 는 start 보다 뒤여야 합니다.")
        return self

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


class CountPoint(BaseModel):
    """`count_over_time()` 의 한 점."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    value: float
    #: 시리즈를 구분하는 라벨 집합 (예: {"service": "payment-api"})
    labels: dict[str, str] = Field(default_factory=dict)


class CountSeries(BaseModel):
    """`count_over_time()` 반환 타입.

    건수·추이는 로그 라인을 세지 않고 metric 쿼리로 따로 구한다 — 라인 조회에는
    정책 상한과 소스 자체 한도가 걸려 있어 오류 폭증 시 실제보다 적게 나온다.
    """

    model_config = ConfigDict(extra="forbid")

    step_seconds: int
    points: list[CountPoint] = Field(default_factory=list)
    warnings: list[FetchWarning] = Field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(point.value for point in self.points)


class ConnectionTestResult(BaseModel):
    """`test_connection()` 공통 반환 타입 (로그 소스·LLM 양쪽)."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    message: str = ""
    latency_ms: int | None = None
    #: 소스/프로바이더가 알려준 부가 정보 (버전, 사용 가능 모델 등)
    details: dict = Field(default_factory=dict)


__all__ = [
    "ConnectionTestResult",
    "CountPoint",
    "CountSeries",
    "FetchResult",
    "FetchWarning",
    "LogRecord",
    "TimeRange",
]
