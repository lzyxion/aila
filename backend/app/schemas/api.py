"""REST API 요청/응답 모델 (설계 문서 "API 초안").

계약상 제약:
- 저장된 secret·API 키는 **어떤 응답에도 평문으로 싣지 않는다.** 존재 여부(`has_secret`)
  또는 마스킹된 값(`api_key_masked`)만 노출한다.
- 화면으로 나가는 로그 라인은 전부 마스킹된 값이다 (`masked_log`, `sample_lines`).
- `estimated_cost` 는 추정값이다. 응답을 정산 근거로 쓰지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.enums import (
    AnalysisJobStatus,
    AuthType,
    LLMProviderName,
    QueryRunStatus,
    Severity,
    SourceType,
    UsageStatus,
)
from app.schemas.analysis import AnalysisResultSchema
from app.schemas.logrecord import CountPoint, FetchWarning

ORM = ConfigDict(from_attributes=True)


class ErrorResponse(BaseModel):
    """공통 오류 응답 (FastAPI HTTPException 형식)."""

    detail: str


class ConnectionTestResponse(BaseModel):
    """연결 테스트 결과. 로그 소스·LLM 공통."""

    ok: bool
    message: str = ""
    latency_ms: int | None = None
    details: dict = Field(default_factory=dict)


# ============================================================ loki connections


class LokiConnectionBase(BaseModel):
    name: str = Field(max_length=128)
    source_type: SourceType = SourceType.LOKI
    base_url: str = Field(max_length=512)
    auth_type: AuthType = AuthType.NONE
    #: 표준 필드명 -> 소스 라벨명 매핑 (권장, models.py 와 동일 방향).
    #: 예: {"service": "app", "environment": "env"}
    #: 어댑터(resolve_label_mapping)는 반대 방향도 수용하지만 새 데이터는 이 방향으로 쓴다.
    label_mapping: dict[str, str] = Field(default_factory=dict)
    active: bool = True


class LokiConnectionCreate(LokiConnectionBase):
    #: 평문 입력 전용. 저장 시 암호화되며 응답에는 절대 포함되지 않는다.
    secret: str | None = None


class LokiConnectionUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    auth_type: AuthType | None = None
    secret: str | None = None
    label_mapping: dict[str, str] | None = None
    active: bool | None = None


class LokiConnectionRead(LokiConnectionBase):
    model_config = ORM

    id: int
    has_secret: bool = False
    created_at: datetime
    updated_at: datetime


class LokiConnectionTestRequest(BaseModel):
    """저장된 연결(`connection_id`) 또는 아직 저장하지 않은 입력값으로 테스트한다."""

    connection_id: int | None = None
    base_url: str | None = None
    auth_type: AuthType = AuthType.NONE
    secret: str | None = None


class LabelValuesResponse(BaseModel):
    """정책 작성 UI 용 라벨 탐색 결과."""

    labels: list[str] = Field(default_factory=list)
    #: 라벨명 -> 값 목록 (프로바이더가 값 탐색을 지원할 때만 채운다)
    values: dict[str, list[str]] = Field(default_factory=dict)
    supports_label_discovery: bool = True


# ============================================================= llm connections


class LLMConnectionBase(BaseModel):
    name: str = Field(max_length=128)
    provider: LLMProviderName
    model: str = Field(max_length=128)
    base_url: str | None = Field(default=None, max_length=512)
    is_default: bool = False
    active: bool = True


class LLMConnectionCreate(LLMConnectionBase):
    #: 평문 입력 전용. 저장 시 암호화된다.
    api_key: str | None = None


class LLMConnectionUpdate(BaseModel):
    name: str | None = None
    provider: LLMProviderName | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    is_default: bool | None = None
    active: bool | None = None


class LLMConnectionRead(LLMConnectionBase):
    model_config = ORM

    id: int
    #: 마스킹된 표시용 값 (예: "****abcd"). 평문 아님.
    api_key_masked: str | None = None
    created_at: datetime
    updated_at: datetime


class LLMConnectionTestRequest(BaseModel):
    """연결 테스트도 실제 과금 호출이다 — 최소 토큰으로 보낸다."""

    connection_id: int | None = None
    provider: LLMProviderName | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None


# =================================================================== policies


class PolicyBase(BaseModel):
    loki_connection_id: int
    name: str = Field(max_length=128)
    description: str | None = None
    #: 소스 고유 문법 그대로 저장한다 (공통 DSL 로 번역하지 않는다).
    logql: str = Field(min_length=1)
    default_range_minutes: int = Field(default=60, gt=0)
    max_lines: int = Field(default=1000, gt=0)
    #: 제외 정규식 목록
    exclusions: list[str] = Field(default_factory=list)
    max_samples_per_group: int = Field(default=3, gt=0)
    allow_ai_analysis: bool = True
    #: 정책별 일일 분석 상한. None 이면 전역 한도만 적용.
    daily_analysis_limit: int | None = Field(default=None, ge=0)


class PolicyCreate(PolicyBase):
    pass


class PolicyUpdate(BaseModel):
    loki_connection_id: int | None = None
    name: str | None = None
    description: str | None = None
    logql: str | None = None
    default_range_minutes: int | None = Field(default=None, gt=0)
    max_lines: int | None = Field(default=None, gt=0)
    exclusions: list[str] | None = None
    max_samples_per_group: int | None = Field(default=None, gt=0)
    allow_ai_analysis: bool | None = None
    daily_analysis_limit: int | None = Field(default=None, ge=0)
    active: bool | None = None


class PolicyRead(PolicyBase):
    model_config = ORM

    id: int
    active: bool
    created_at: datetime
    updated_at: datetime


class PolicyPreviewRequest(BaseModel):
    """저장 전 실행 결과 미리보기. 잘못 쓴 쿼리가 정책으로 굳는 것을 막는다."""

    loki_connection_id: int
    logql: str = Field(min_length=1)
    range_minutes: int = Field(default=60, gt=0)
    limit: int = Field(default=50, gt=0)
    exclusions: list[str] = Field(default_factory=list)


class PolicyPreviewResponse(BaseModel):
    """미리보기 결과. `sample_lines` 는 화면 표시용이므로 이미 마스킹된 값이다."""

    fetched: int = 0
    dropped: int = 0
    truncated: bool = False
    warnings: list[FetchWarning] = Field(default_factory=list)
    sample_lines: list[str] = Field(default_factory=list)


# ================================================================= query runs


class QueryRunCreateRequest(BaseModel):
    """정책 실행. 기간을 주지 않으면 정책의 `default_range_minutes` 를 쓴다.

    `limit` 은 정책 `max_lines` 를 넘길 수 없다 — 상한은 서버가 강제한다.
    """

    range_start: datetime | None = None
    range_end: datetime | None = None
    limit: int | None = Field(default=None, gt=0)


class QueryRunRead(BaseModel):
    model_config = ORM

    id: int
    policy_id: int
    status: QueryRunStatus
    started_at: datetime
    finished_at: datetime | None = None
    range_start: datetime
    range_end: datetime
    fetched_count: int = 0
    dropped_count: int = 0
    warnings: list[FetchWarning] = Field(default_factory=list)
    error_message: str | None = None
    group_count: int = 0


# =============================================================== error groups


class ErrorGroupSummary(BaseModel):
    """그룹 목록 항목. 전체 원문이 아니라 대표 메시지·횟수·발생 시각만 준다.

    `analysis_status` 는 현재 조회의 그룹 id 가 아니라 **fingerprint 기준**이다 —
    이전 조회에서 이미 분석된 그룹을 중복 요청(=중복 과금)하지 않게 하기 위해서다.
    """

    model_config = ORM

    id: int
    query_run_id: int
    fingerprint: str
    service: str | None = None
    environment: str | None = None
    error_type: str | None = None
    normalized_message: str
    count: int
    first_seen: datetime
    last_seen: datetime
    analysis_status: AnalysisJobStatus | None = None
    latest_analysis_job_id: int | None = None
    latest_severity: Severity | None = None


class ErrorGroupListResponse(BaseModel):
    query_run_id: int
    total: int = 0
    items: list[ErrorGroupSummary] = Field(default_factory=list)


class ErrorSampleRead(BaseModel):
    """마스킹된 대표 로그. 원본은 저장하지 않으므로 여기에도 없다."""

    model_config = ORM

    id: int
    occurred_at: datetime
    masked_log: str
    labels: dict[str, str] = Field(default_factory=dict)
    stacktrace: str | None = None
    masking_rule_version: str = "v1"


class AnalysisJobSummary(BaseModel):
    model_config = ORM

    id: int
    status: AnalysisJobStatus
    provider: str
    model: str
    prompt_version: str
    requested_at: datetime
    completed_at: datetime | None = None
    severity: Severity | None = None
    summary: str | None = None


class ErrorGroupDetail(ErrorGroupSummary):
    labels: dict[str, str] = Field(default_factory=dict)
    top_stack_frame: str | None = None
    normalization_rule_version: str = "v1"
    samples: list[ErrorSampleRead] = Field(default_factory=list)
    #: 발생 추이 (metric 쿼리 기반)
    trend: list[CountPoint] = Field(default_factory=list)
    #: 같은 fingerprint 의 과거 분석 이력 (조회 회차를 넘어 조인)
    analyses: list[AnalysisJobSummary] = Field(default_factory=list)


# ============================================================== analysis jobs


class AnalysisJobCreateRequest(BaseModel):
    """분석 시작. `llm_connection_id` 를 주지 않으면 기본 연결을 쓴다."""

    llm_connection_id: int | None = None


class UsageRecordRead(BaseModel):
    model_config = ORM

    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    #: 추정값. 계산 시점 단가표를 pricing_snapshot 에 함께 남긴다.
    estimated_cost: Decimal | None = None
    pricing_snapshot: dict | None = None
    latency_ms: int | None = None
    status: UsageStatus = UsageStatus.SUCCEEDED
    failure_reason: str | None = None


class AnalysisJobRead(BaseModel):
    model_config = ORM

    id: int
    error_group_id: int
    llm_connection_id: int | None = None
    fingerprint: str
    status: AnalysisJobStatus
    #: 실행 시점 값으로 고정 저장된다 (연결 설정이 바뀌어도 이력은 유지).
    provider: str
    model: str
    prompt_version: str
    requested_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    result: AnalysisResultSchema | None = None
    usage: UsageRecordRead | None = None


class AnalysisJobCreateResponse(AnalysisJobRead):
    """분석 시작은 멱등이다 — 진행 중인 작업이 있으면 `reused=True` 로 기존 작업을 반환한다."""

    reused: bool = False


class AnalysisJobListItem(AnalysisJobSummary):
    """분석 작업 목록 항목.

    목록은 조회 회차와 무관하게 보여야 하므로 그룹 메타데이터를 값으로 함께 싣는다
    (`error_groups` 는 조회 1 회 범위이고, 추적 기준은 `fingerprint` 다).
    """

    error_group_id: int
    fingerprint: str
    llm_connection_id: int | None = None
    service: str | None = None
    environment: str | None = None
    error_type: str | None = None
    normalized_message: str | None = None
    error_message: str | None = None


class AnalysisJobListResponse(BaseModel):
    """최신순 페이지네이션 목록 (프런트 폴링·이력 화면용)."""

    total: int = 0
    limit: int = 20
    offset: int = 0
    items: list[AnalysisJobListItem] = Field(default_factory=list)


# ================================================================== dashboard


class ServiceErrorCount(BaseModel):
    service: str | None = None
    count: float = 0.0


class DashboardOverviewResponse(BaseModel):
    """오류 추이·상위 그룹. 건수는 metric 쿼리 기반이며 로그 라인 수가 아니다."""

    policy_id: int | None = None
    query_run_id: int | None = None
    range_start: datetime
    range_end: datetime
    step_seconds: int = 300
    total_errors: float = 0.0
    series: list[CountPoint] = Field(default_factory=list)
    by_service: list[ServiceErrorCount] = Field(default_factory=list)
    top_groups: list[ErrorGroupSummary] = Field(default_factory=list)
    warnings: list[FetchWarning] = Field(default_factory=list)


# ====================================================================== usage


class UsageAggregate(BaseModel):
    provider: str
    model: str
    job_count: int = 0
    failure_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: Decimal = Decimal("0")
    avg_latency_ms: float | None = None


class UsageResponse(BaseModel):
    range_start: datetime
    range_end: datetime
    items: list[UsageAggregate] = Field(default_factory=list)
    total_jobs: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    #: 추정 합계. 정산 근거가 아니다.
    total_estimated_cost: Decimal = Decimal("0")


__all__ = [
    "AnalysisJobCreateRequest",
    "AnalysisJobCreateResponse",
    "AnalysisJobListItem",
    "AnalysisJobListResponse",
    "AnalysisJobRead",
    "AnalysisJobSummary",
    "ConnectionTestResponse",
    "DashboardOverviewResponse",
    "ErrorGroupDetail",
    "ErrorGroupListResponse",
    "ErrorGroupSummary",
    "ErrorResponse",
    "ErrorSampleRead",
    "LLMConnectionCreate",
    "LLMConnectionRead",
    "LLMConnectionTestRequest",
    "LLMConnectionUpdate",
    "LabelValuesResponse",
    "LokiConnectionCreate",
    "LokiConnectionRead",
    "LokiConnectionTestRequest",
    "LokiConnectionUpdate",
    "PolicyCreate",
    "PolicyPreviewRequest",
    "PolicyPreviewResponse",
    "PolicyRead",
    "PolicyUpdate",
    "QueryRunCreateRequest",
    "QueryRunRead",
    "ServiceErrorCount",
    "UsageAggregate",
    "UsageRecordRead",
    "UsageResponse",
]
