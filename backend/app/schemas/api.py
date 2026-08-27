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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.enums import (
    AnalysisJobStatus,
    AuthType,
    LLMProviderName,
    QueryRunStatus,
    Severity,
    SourceType,
    TriggeredBy,
    UsageStatus,
    UserRole,
)
from app.schemas.analysis import AnalysisResultSchema
from app.schemas.logrecord import CountPoint, FetchWarning

ORM = ConfigDict(from_attributes=True)


class ErrorResponse(BaseModel):
    """공통 오류 응답 (FastAPI HTTPException 형식)."""

    detail: str


# ======================================================================= auth


class LoginRequest(BaseModel):
    """`POST /auth/login`. 비밀번호는 바디로만 받는다 (쿼리스트링 금지)."""

    username: str = Field(max_length=128)
    password: str


class UserRead(BaseModel):
    """로그인·`/auth/me`·계정 생성의 공통 응답.

    계정 식별에 필요한 최소값만 싣는다 — id 도 해시도 내보내지 않는다.
    """

    model_config = ORM

    username: str
    role: UserRole


class UserCreateRequest(BaseModel):
    """`POST /auth/users` (admin 전용). viewer 계정을 만들기 위한 최소 입력."""

    username: str = Field(max_length=128)
    password: str = Field(min_length=1)
    role: UserRole = UserRole.VIEWER


class UserDetail(UserRead):
    """계정 관리 화면용 항목 (Phase 6, admin 전용 경로에서만 나간다).

    `UserRead` 는 로그인·`/auth/me` 응답이라 "누구인가" 만 싣는다. 여기서는 관리
    화면이 행을 식별하고(`id`) 상태를 보여줘야(`active`) 하므로 그만큼만 더 싣는다 —
    비밀번호 해시는 어떤 경로로도 나가지 않는다.
    """

    model_config = ORM

    id: int
    active: bool = True
    created_at: datetime


class UserListResponse(BaseModel):
    """`GET /auth/users` (admin 전용). 목록 봉투는 다른 목록 API 와 같은 모양이다."""

    total: int = 0
    items: list[UserDetail] = Field(default_factory=list)


class UserUpdateRequest(BaseModel):
    """`PATCH /auth/users/{id}` (admin 전용). 준 필드만 바꾼다.

    안전 규칙은 서비스가 강제한다 — 마지막 남은 active admin 의 강등·비활성은 409,
    자기 자신 비활성도 409. `active=false` 와 `password` 변경은 그 계정의 세션을
    전부 무효화한다 (비활성 계정의 쿠키가 남은 12 시간 동안 살아 있으면 안 된다).
    """

    role: UserRole | None = None
    active: bool | None = None
    #: 새 비밀번호 (평문 입력 전용). 저장은 기존 scrypt 형식 그대로다.
    password: str | None = Field(default=None, min_length=1)


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
    #: 로그를 내보내고 있어야 정상인 서비스 이름 목록 (Phase 7).
    #: 비어 있으면 수집 중단 확인을 하지 않는다. 값은 **표준 필드 `service` 기준**이다
    #: (소스 라벨명이 달라도 label_mapping 이 흡수한다).
    expected_services: list[str] = Field(default_factory=list)

    @field_validator("expected_services", mode="before")
    @classmethod
    def _none_as_empty(cls, value: object) -> object:
        # DB 컬럼(0005)은 nullable 이다 — ORM 의 None 을 "설정 안 함"(빈 목록)으로 읽는다.
        return [] if value is None else value


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
    #: 빈 목록을 주면 수집 중단 확인을 끈다. None 은 "변경 없음"이다.
    expected_services: list[str] | None = None


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


class LLMModelListRequest(BaseModel):
    """`POST /llm-connections/models` — 모델 목록 조회 입력.

    **바디로 받는 이유**: `api_key` 를 쿼리스트링에 실으면 평문 키가 액세스 로그·프록시
    로그·브라우저 히스토리에 남는다. 조회(무과금)라도 비밀을 실어 보내는 요청은 POST 다.

    입력 규칙은 `LLMConnectionTestRequest` 와 같다 — `connection_id` 를 주면 저장된 값을
    쓰고, 함께 넘어온 provider/base_url/api_key 가 있으면 그쪽이 이긴다.

    `provider` 가 열거형이 아니라 `str` 인 것은 의도다. 모르는 이름을 pydantic 이 422 로
    튕기면 프론트의 `isEndpointMissing` 폴백이 "경로 없음"으로 오해한다 — 라우터에서
    **사유가 담긴 400** 으로 바꿔 준다.
    """

    connection_id: int | None = None
    provider: str | None = Field(default=None, max_length=64)
    base_url: str | None = Field(default=None, max_length=512)
    #: 평문 입력 전용. 저장하지 않고, 응답에도 오류 detail 에도 싣지 않는다.
    api_key: str | None = None


class LLMModelListResponse(BaseModel):
    """`POST /llm-connections/models` — 프로바이더가 제공하는 모델 id 목록.

    모델 목록 조회는 토큰을 쓰지 않는 **무과금** 호출이다.
    순서는 프로바이더가 준 그대로다 (정렬은 표시 계층의 몫).
    응답에 API 키는 어떤 형태로도 싣지 않는다 — 마스킹된 값조차 넣지 않는다.
    """

    provider: LLMProviderName
    models: list[str] = Field(default_factory=list)


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
    #: 유입량·오류 비율의 **분모 쿼리** (Phase 7). 오류 셀렉터와 같은 라벨 범위의
    #: 전체 로그를 세는 소스 고유 문법 쿼리. None 이면 유입량·비율을 계산하지 않는다.
    baseline_query: str | None = None

    # --- 스케줄 (Phase 5) ---
    #: 켜면 스케줄러가 주기 실행한다. `schedule_interval_minutes` 가 함께 있어야 한다(422).
    schedule_enabled: bool = False
    #: 실행 주기(분).
    schedule_interval_minutes: int | None = Field(default=None, gt=0)
    #: 스케줄 실행 직후 **분석 이력이 전혀 없는 fingerprint** 만 자동 분석한다.
    #: 상한은 기존 장치가 그대로 건다 (allow_ai_analysis · 멱등 · 일일 한도 429).
    auto_analyze_new: bool = False


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
    #: `daily_analysis_limit` 와 같은 관례 — 명시적 null 로 지운다 (exclude_unset).
    baseline_query: str | None = None
    schedule_enabled: bool | None = None
    schedule_interval_minutes: int | None = Field(default=None, gt=0)
    auto_analyze_new: bool | None = None
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
    #: 미리보기는 "쿼리가 무엇을 잡는가"만 확인하면 된다. 상한을 두지 않으면
    #: 저장도 하지 않은 쿼리로 정책 실행과 같은 양을 읽게 된다.
    limit: int = Field(default=50, gt=0, le=200)
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
    #: 이 실행을 시작한 주체 (Phase 5). 화면 배지용이며 동작을 분기하지 않는다.
    triggered_by: TriggeredBy = TriggeredBy.MANUAL


class QueryRunListResponse(BaseModel):
    """`GET /policies/{id}/query-runs` — 정책별 실행 이력 페이지 (최신순).

    `total` 은 페이지가 아니라 **정책 전체**의 건수다 (페이지네이션 UI 가 필요로 한다).
    """

    total: int = 0
    limit: int = 0
    offset: int = 0
    items: list[QueryRunRead] = Field(default_factory=list)


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
    #: 이 분석을 시작한 주체 (Phase 5).
    triggered_by: TriggeredBy = TriggeredBy.MANUAL


class ErrorGroupDetail(ErrorGroupSummary):
    labels: dict[str, str] = Field(default_factory=dict)
    top_stack_frame: str | None = None
    normalization_rule_version: str = "v1"
    samples: list[ErrorSampleRead] = Field(default_factory=list)
    #: 발생 추이 (metric 쿼리 기반)
    trend: list[CountPoint] = Field(default_factory=list)
    #: `trend` 가 비어 있는 사유. 조회 실패와 "발생이 없었다"를 구분한다.
    trend_warnings: list[FetchWarning] = Field(default_factory=list)
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
    #: 이 분석을 시작한 주체 (Phase 5).
    triggered_by: TriggeredBy = TriggeredBy.MANUAL
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
    """오류 추이·상위 그룹. 건수는 metric 쿼리 기반이며 로그 라인 수가 아니다.

    Phase 7 추가 필드 규칙:
    - `series` 는 **시각별로 합산된** 포인트다 (같은 timestamp 는 한 번만 나온다).
      서비스별 분해는 `by_service` 가 담당한다.
    - `group_count`·`unanalyzed_group_count` 는 조회 회차 전체의 `COUNT` 다 —
      `top_groups` 목록 길이(상위 N)와 다르다.
    - `ingest_total`·`error_ratio` 는 정책의 `baseline_query` 가 있을 때만 계산한다.
      실패·미설정은 **0 이 아니라 null** 이다 (0 은 "유입이 없었다"로 읽힌다).
    """

    policy_id: int | None = None
    query_run_id: int | None = None
    range_start: datetime
    range_end: datetime
    step_seconds: int = 300
    total_errors: float = 0.0
    series: list[CountPoint] = Field(default_factory=list)
    by_service: list[ServiceErrorCount] = Field(default_factory=list)
    top_groups: list[ErrorGroupSummary] = Field(default_factory=list)
    #: 이 조회 회차의 전체 오류 그룹 수 (DB COUNT). 회차가 없으면 null.
    group_count: int | None = None
    #: 그중 fingerprint 분석 이력이 전혀 없는 그룹 수 (DB COUNT). 회차가 없으면 null.
    unanalyzed_group_count: int | None = None
    #: 같은 기간·step 의 분모 쿼리(`baseline_query`) 총 건수. 미설정·실패 시 null.
    ingest_total: float | None = None
    #: 분모 쿼리의 시각별 합산 시리즈. `ingest_total` 이 null 이면 빈 배열.
    ingest_series: list[CountPoint] = Field(default_factory=list)
    #: total_errors / ingest_total. 분모가 null 이거나 0 이면 null.
    error_ratio: float | None = None
    warnings: list[FetchWarning] = Field(default_factory=list)


class DashboardSummaryLastRun(BaseModel):
    """정책의 가장 최근 조회 이력 (성공·실패 무관). 없으면 상위에서 `null` 이다."""

    id: int
    started_at: datetime
    status: QueryRunStatus
    fetched_count: int = 0
    group_count: int = 0
    warnings: list[FetchWarning] = Field(default_factory=list)


class DashboardPolicySummary(BaseModel):
    """통합 대시보드의 정책 한 줄.

    `total_errors_24h` 는 정책별 `count_over_time` 결과이며 **실패하면 0 이 아니라
    `null`** 이다 (0 은 "오류가 없었다"로 읽힌다). 사유는 `warnings` 의 코드로 남는다.
    """

    policy_id: int
    name: str
    active: bool = True
    schedule_enabled: bool = False
    schedule_interval_minutes: int | None = None
    last_run: DashboardSummaryLastRun | None = None
    #: 최근 **성공** 조회의 그룹 중 fingerprint 분석 이력이 전혀 없는 그룹 수.
    unanalyzed_group_count: int = 0
    total_errors_24h: float | None = None
    #: `total_errors_24h` 를 만든 **바로 그** `count_over_time` 결과의 포인트 (step 3600).
    #: 추가 metric 호출을 하지 않으므로, 건수 조회가 실패하면 여기도 빈 배열이다.
    series_24h: list[CountPoint] = Field(default_factory=list)
    warnings: list[FetchWarning] = Field(default_factory=list)


class DashboardSummaryResponse(BaseModel):
    """`GET /dashboard/summary` — 정책 전체를 한 화면에 모은 운영 요약.

    정책 상세(추이·상위 그룹)는 기존 `/dashboard/overview` 가 그대로 담당한다.
    """

    generated_at: datetime
    policies: list[DashboardPolicySummary] = Field(default_factory=list)


class DashboardErrorGroupItem(ErrorGroupSummary):
    """`GET /dashboard/error-groups` 항목 — 그룹 요약 + 어느 정책에서 나왔는가.

    그룹 자체는 조회 1 회 범위이므로, 정책을 넘나드는 목록에서는 출처를 값으로
    함께 실어야 화면에서 "이게 어느 정책의 오류인가"를 되짚을 수 있다.
    """

    policy_id: int
    policy_name: str


class DashboardErrorGroupListResponse(BaseModel):
    """전 활성 정책의 **최신 성공 조회** 그룹을 한데 모은 목록 (count 내림차순)."""

    total: int = 0
    limit: int = 0
    offset: int = 0
    items: list[DashboardErrorGroupItem] = Field(default_factory=list)


# ====================================================================== usage


class UsageAggregate(BaseModel):
    provider: str
    model: str
    job_count: int = 0
    failure_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    #: 단가표에 모델이 없으면 **None** 이다. 0 은 "쌌다"로 읽히므로 쓰지 않는다.
    estimated_cost: Decimal | None = None
    avg_latency_ms: float | None = None


class UsageBucket(BaseModel):
    """`group_by` 분해 한 줄 (일별 또는 정책별).

    `estimated_cost` 규칙은 `UsageAggregate` 와 같다 — 계산 가능한 기록이 하나도
    없으면 **0 이 아니라 None** 이다 (0 은 "쌌다"로 읽힌다).
    """

    #: day → "YYYY-MM-DD"(app_settings.timezone 로컬 날짜) / policy → policy_id 문자열
    #: (정책 연결이 끊긴 작업은 "unknown").
    key: str
    #: 화면 표시용 이름. day 는 key 와 같고, policy 는 정책명이다.
    label: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: Decimal | None = None
    job_count: int = 0
    failure_count: int = 0


class UsageResponse(BaseModel):
    range_start: datetime
    range_end: datetime
    items: list[UsageAggregate] = Field(default_factory=list)
    #: `group_by` 를 준 요청에만 채운다. 생략하면 **None** 이다 (빈 배열이 아니다 —
    #: "분해를 요청하지 않았다"와 "분해했더니 아무것도 없었다"는 다른 상태다).
    buckets: list[UsageBucket] | None = None
    total_jobs: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    #: 추정 합계. 정산 근거가 아니다.
    #: 비용을 계산할 수 있는 항목이 하나도 없으면 0 이 아니라 **None** 이다.
    total_estimated_cost: Decimal | None = None


class PolicyDailyUsage(BaseModel):
    """`GET /usage/daily-limit` 의 정책 한 줄 — **자체 한도를 가진 정책만** 싣는다.

    한도가 없는 정책은 전역 게이지에 이미 포함되어 있다. 여기 실으면 "정책마다
    별도 상한이 있다"로 오해하게 만든다.
    """

    policy_id: int
    name: str
    limit: int = Field(ge=0)
    used: int = 0


class DailyLimitResponse(BaseModel):
    """`GET /usage/daily-limit` — 오늘의 분석 한도 소진 현황 게이지.

    "오늘"의 경계는 분석 한도와 동일하다: `app_settings.timezone` 의 로컬 자정
    (`analysis.service.daily_usage` 와 같은 계산을 쓴다 — 게이지와 429 가 다른
    숫자를 보면 게이지를 믿을 수 없다).
    """

    #: 기준 로컬 날짜 ("YYYY-MM-DD")와 그 타임존 이름.
    date: str
    timezone: str
    global_limit: int = Field(ge=0)
    global_used: int = 0
    #: 자체 `daily_analysis_limit` 를 가진 정책들의 소진 현황.
    policies: list[PolicyDailyUsage] = Field(default_factory=list)


# ================================================================== settings


class AppSettingRead(BaseModel):
    """`app_settings` 한 줄. `value` 가 None 이면 서버 기본값을 쓴다는 뜻이다."""

    key: str
    value: dict | list | str | int | float | bool | None = None
    description: str | None = None
    updated_at: datetime | None = None
    #: 행이 없을 때 실제로 적용되는 값 (설정 기본값).
    effective_value: dict | list | str | int | float | bool | None = None


class AppSettingListResponse(BaseModel):
    items: list[AppSettingRead] = Field(default_factory=list)


class AppSettingUpdate(BaseModel):
    """예약 키만 쓸 수 있다. 값 형식은 키마다 서버가 검증한다."""

    value: dict | list | str | int | float | bool | None = None


# =============================================================== maintenance


class SamplePurgeResponse(BaseModel):
    """보존 기간이 지난 `error_samples` 삭제 결과."""

    deleted: int = 0
    retention_days: int = 0
    cutoff: datetime | None = None
    #: 하루 1 회 자동 실행 조건에 걸려 실제로는 돌지 않았을 때 False.
    executed: bool = True


__all__ = [
    "AnalysisJobCreateRequest",
    "AnalysisJobCreateResponse",
    "AnalysisJobListItem",
    "AnalysisJobListResponse",
    "AnalysisJobRead",
    "AnalysisJobSummary",
    "AppSettingListResponse",
    "AppSettingRead",
    "AppSettingUpdate",
    "ConnectionTestResponse",
    "DashboardErrorGroupItem",
    "DashboardErrorGroupListResponse",
    "DashboardOverviewResponse",
    "DashboardPolicySummary",
    "DashboardSummaryLastRun",
    "DashboardSummaryResponse",
    "ErrorGroupDetail",
    "ErrorGroupListResponse",
    "ErrorGroupSummary",
    "ErrorResponse",
    "ErrorSampleRead",
    "LLMConnectionCreate",
    "LLMConnectionRead",
    "LLMConnectionTestRequest",
    "LLMConnectionUpdate",
    "LLMModelListRequest",
    "LLMModelListResponse",
    "LabelValuesResponse",
    "LoginRequest",
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
    "QueryRunListResponse",
    "QueryRunRead",
    "SamplePurgeResponse",
    "ServiceErrorCount",
    "UsageAggregate",
    "UsageBucket",
    "UsageRecordRead",
    "UsageResponse",
    "UserCreateRequest",
    "UserDetail",
    "UserListResponse",
    "UserRead",
    "UserUpdateRequest",
]
