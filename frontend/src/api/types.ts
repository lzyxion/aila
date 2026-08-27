/**
 * backend/app/schemas/api.py · analysis.py · logrecord.py · enums.py 를 손으로 옮긴 타입.
 *
 * 백엔드 스키마 파일은 동결(freeze) 상태다. 여기 있는 타입이 어긋나면 이 파일을 고치고
 * 백엔드는 건드리지 않는다 — 계약 자체에 문제가 있으면 보고한다.
 *
 * 표시 규칙(계약상 제약):
 * - 저장된 secret·API 키는 어떤 응답에도 평문으로 오지 않는다 (`has_secret` / `api_key_masked`).
 * - 화면으로 나오는 로그 라인은 전부 마스킹된 값이다 (`masked_log`, `sample_lines`).
 * - `estimated_cost` 는 추정값이다. 화면에서 "추정" 표기를 유지한다.
 * - `severity` 는 "LLM 추정 심각도"이며 발생량 기반 지표와 분리해 표시한다.
 * - `confidence` 는 정렬용 힌트일 뿐 확률로 표기하지 않는다.
 *
 * datetime 은 JSON 위에서 ISO 8601 문자열로 온다.
 */

// ==================================================================== enums

/** app.enums.SourceType — MVP 는 loki 하나만 구현한다. */
export type SourceType = 'loki';
export const SOURCE_TYPES: SourceType[] = ['loki'];

/** app.enums.AuthType */
export type AuthType = 'none' | 'basic' | 'bearer' | 'header';
export const AUTH_TYPES: AuthType[] = ['none', 'basic', 'bearer', 'header'];

/** app.enums.LLMProviderName */
export type LLMProviderName = 'openai' | 'anthropic' | 'openai_compatible';
export const LLM_PROVIDERS: LLMProviderName[] = ['openai', 'anthropic', 'openai_compatible'];

/** app.enums.QueryRunStatus */
export type QueryRunStatus = 'pending' | 'running' | 'succeeded' | 'failed';

/** app.enums.AnalysisJobStatus */
export type AnalysisJobStatus = 'pending' | 'running' | 'succeeded' | 'failed';

/** app.enums.UsageStatus */
export type UsageStatus = 'succeeded' | 'failed';

/** app.enums.Severity — LLM 이 추정한 심각도. 발생량 기반 지표가 아니다. */
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';

/**
 * 실행 주체. 이력 화면의 배지로만 쓴다 — "이 조회·분석을 사람이 눌렀는가, 스케줄러가
 * 돌렸는가"를 나중에 되짚을 수 있어야 비용 추적이 된다.
 *
 * 백엔드가 아직 이 필드를 내려주지 않을 수 있으므로 소비하는 쪽은 `undefined` 를
 * `manual` 과 같게 다루지 말고 **배지를 생략**한다 (`triggeredByLabel` 참고).
 */
export type TriggeredBy = 'manual' | 'schedule';

/** app.enums.ACTIVE_JOB_STATUSES — 진행 중으로 간주하는 상태 (폴링 대상). */
export const ACTIVE_JOB_STATUSES: AnalysisJobStatus[] = ['pending', 'running'];

export function isActiveJobStatus(status: AnalysisJobStatus | null | undefined): boolean {
  return status === 'pending' || status === 'running';
}

// ============================================================== logrecord.py

/** app.schemas.logrecord.FetchWarning */
export interface FetchWarning {
  /** 예: parse_error, limit_reached, partial_range, entry_out_of_order */
  code: string;
  message: string;
  count?: number | null;
}

/** app.schemas.logrecord.CountPoint — count_over_time() 의 한 점. */
export interface CountPoint {
  /** ISO 8601 */
  timestamp: string;
  value: number;
  labels: Record<string, string>;
}

// =============================================================== analysis.py

/** app.schemas.analysis.Hypothesis */
export interface Hypothesis {
  cause: string;
  /** 정렬용 힌트. 확률이 아니다. */
  confidence: number;
  /** 로그에서 근거가 된 조각 (마스킹된 값) */
  evidence: string[];
}

/** app.schemas.analysis.AnalysisResultSchema — hypotheses·limitations 는 필수다. */
export interface AnalysisResultSchema {
  summary: string;
  severity: Severity;
  /** 최소 1 개 — 단정 금지 장치. */
  hypotheses: Hypothesis[];
  investigation_steps: string[];
  mitigation: string[];
  /** 최소 1 개 — 로그만으로 알 수 없는 것을 명시. */
  limitations: string[];
}

// ======================================================================= auth

/**
 * app.enums.UserRole — `viewer` 는 **GET 만** 할 수 있다.
 *
 * 판정은 서버가 한다 (viewer 의 비-GET 요청은 403). 화면이 쓰기 UI 를 감추는 것은
 * 편의일 뿐 보안 장치가 아니다 — 기간·라인 수 상한과 같은 규칙이다.
 */
export type UserRole = 'admin' | 'viewer';

/** `GET /api/auth/me` · `POST /api/auth/login` 응답. 비밀번호는 어느 응답에도 없다. */
export interface AuthUser {
  username: string;
  role: UserRole;
}

/**
 * `POST /api/auth/login` — 성공하면 httpOnly 세션 쿠키(SameSite=Lax)가 붙는다.
 * 토큰을 응답 본문으로 받지 않는다 — 자바스크립트가 읽을 수 있는 자리에 두면
 * XSS 하나로 세션이 통째로 넘어간다.
 */
export interface LoginRequest {
  username: string;
  password: string;
}

/**
 * `GET /api/auth/users` 의 항목 (admin 전용) — 백엔드의 `schemas.api.UserDetail`.
 *
 * 백엔드 쪽 `UserRead` 는 로그인·`/auth/me` 응답이라 `{username, role}` 뿐이다(프런트에서는
 * `AuthUser`). 관리 화면은 행을 식별하고(`id`) 상태를 보여줘야(`active`) 하므로 그만큼만 더
 * 실린다 — 비밀번호 해시는 어떤 경로로도 나오지 않는다.
 *
 * `active=false` 는 **삭제가 아니라 비활성**이고, 그 계정의 세션은 서버가 전부 무효화한다.
 */
export interface UserRead {
  id: number;
  username: string;
  role: UserRole;
  active: boolean;
  created_at: string;
}

export interface UserListResponse {
  total: number;
  items: UserRead[];
}

/** `POST /api/auth/users` — 생성 (기존 계약). */
export interface UserCreateRequest {
  username: string;
  password: string;
  role: UserRole;
}

/**
 * 생성 응답은 최소 `{username, role}` 이다 (README 의 인증 계약). 백엔드가 계정 관리를
 * 올리면서 `UserRead` 전체를 주기 시작해도 깨지지 않게 **넓게** 받는다 — 화면은 어차피
 * 목록을 다시 읽는다.
 */
export type UserCreateResponse = AuthUser & Partial<UserRead>;

/**
 * `PATCH /api/auth/users/{id}` — 역할 변경·비활성/재활성·비밀번호 재설정.
 *
 * 세 가지 보호는 **서버가** 판정한다 (화면이 미리 막는 것은 편의다):
 * - 마지막 남은 active admin 의 강등·비활성 → 409
 * - 자기 자신 비활성(DELETE) → 409
 * - `active=false` 또는 `password` 변경 시 그 계정의 세션 전부 무효화
 */
export interface UserUpdateRequest {
  role?: UserRole;
  active?: boolean;
  password?: string;
}

// ============================================================ 공통 API 모델

/** app.schemas.api.ErrorResponse (FastAPI HTTPException 형식) */
export interface ErrorResponse {
  detail: string;
}

/** app.schemas.api.ConnectionTestResponse — 로그 소스·LLM 공통. */
export interface ConnectionTestResponse {
  ok: boolean;
  message: string;
  latency_ms?: number | null;
  details: Record<string, unknown>;
}

// =========================================================== loki connections

export interface LokiConnectionBase {
  name: string;
  source_type: SourceType;
  base_url: string;
  auth_type: AuthType;
  /** 소스 라벨명 -> 표준 필드명 매핑. 예: {"app": "service"} */
  label_mapping: Record<string, string>;
  active: boolean;
  /**
   * 로그를 내보내고 있어야 정상인 서비스 이름 목록 (Phase 7, 표준 필드 `service` 기준).
   * 비어 있으면 수집 중단 확인을 하지 않는다. 조회 실행 시 부재 서비스가
   * `ingest_absent` 경고로 run.warnings 에 남는다.
   */
  expected_services: string[];
}

export interface LokiConnectionCreate extends LokiConnectionBase {
  /** 평문 입력 전용. 응답에는 절대 포함되지 않는다. */
  secret?: string | null;
}

export interface LokiConnectionUpdate {
  name?: string | null;
  base_url?: string | null;
  auth_type?: AuthType | null;
  secret?: string | null;
  label_mapping?: Record<string, string> | null;
  active?: boolean | null;
  /** 빈 배열을 주면 수집 중단 확인을 끈다. 생략은 "변경 없음"이다. */
  expected_services?: string[] | null;
}

export interface LokiConnectionRead extends LokiConnectionBase {
  id: number;
  has_secret: boolean;
  created_at: string;
  updated_at: string;
}

export interface LokiConnectionTestRequest {
  connection_id?: number | null;
  base_url?: string | null;
  auth_type?: AuthType;
  secret?: string | null;
}

/** app.schemas.api.LabelValuesResponse — 정책 작성 UI 용 라벨 탐색 결과. */
export interface LabelValuesResponse {
  labels: string[];
  values: Record<string, string[]>;
  supports_label_discovery: boolean;
}

// ============================================================ llm connections

export interface LLMConnectionBase {
  name: string;
  provider: LLMProviderName;
  model: string;
  base_url?: string | null;
  is_default: boolean;
  active: boolean;
}

export interface LLMConnectionCreate extends LLMConnectionBase {
  /** 평문 입력 전용. 저장 시 암호화된다. */
  api_key?: string | null;
}

export interface LLMConnectionUpdate {
  name?: string | null;
  provider?: LLMProviderName | null;
  model?: string | null;
  base_url?: string | null;
  api_key?: string | null;
  is_default?: boolean | null;
  active?: boolean | null;
}

export interface LLMConnectionRead extends LLMConnectionBase {
  id: number;
  /** 마스킹된 표시용 값 (예: "****abcd"). 평문 아님. */
  api_key_masked?: string | null;
  created_at: string;
  updated_at: string;
}

export interface LLMConnectionTestRequest {
  connection_id?: number | null;
  provider?: LLMProviderName | null;
  model?: string | null;
  base_url?: string | null;
  api_key?: string | null;
}

/**
 * `POST /api/llm-connections/models` — 모델 목록 조회 입력.
 *
 * **조회지만 GET 이 아니다.** `api_key` 를 쿼리스트링으로 보내면 평문 키가 서버
 * 액세스 로그·프록시 로그·브라우저 히스토리에 남는다. 비밀은 바디로만 보낸다.
 *
 * 입력 규칙은 `LLMConnectionTestRequest` 와 같다 — `connection_id` 를 주면 저장된
 * 값을 쓰고, 함께 보낸 provider/base_url/api_key 가 있으면 그쪽이 이긴다.
 */
export interface LLMModelListRequest {
  connection_id?: number | null;
  provider?: LLMProviderName | null;
  base_url?: string | null;
  api_key?: string | null;
}

/**
 * `POST /api/llm-connections/models` — 프로바이더가 제공하는 모델 목록.
 *
 * 실패(502/400)하면 화면은 오류로 막지 않고 **자유 입력으로 폴백**한다. 모델 이름을
 * 목록에서 고르는 것은 편의일 뿐이고, 프로바이더가 목록 API 를 주지 않거나 키가 아직
 * 없을 수도 있기 때문이다.
 */
export interface LLMModelListResponse {
  provider: string;
  models: string[];
}

// ================================================================== policies

export interface PolicyBase {
  loki_connection_id: number;
  name: string;
  description?: string | null;
  /** 소스 고유 문법 그대로 저장한다 (공통 DSL 로 번역하지 않는다). */
  logql: string;
  default_range_minutes: number;
  max_lines: number;
  /** 제외 정규식 목록 */
  exclusions: string[];
  max_samples_per_group: number;
  allow_ai_analysis: boolean;
  /** 정책별 일일 분석 상한. null 이면 전역 한도만 적용. */
  daily_analysis_limit?: number | null;
  /**
   * 유입량·오류 비율의 **분모 쿼리** (Phase 7). 오류 셀렉터와 같은 라벨 범위의
   * 전체 로그를 세는 소스 고유 문법 쿼리. null 이면 대시보드가 유입량·비율을 그리지 않는다.
   */
  baseline_query?: string | null;

  // --- 스케줄 (0004 마이그레이션, additive) ---
  /** 스케줄 조회를 켠다. 끄면 아래 두 값은 무시된다. */
  schedule_enabled?: boolean;
  /** 조회 주기(분). `schedule_enabled` 가 true 일 때만 의미가 있다. */
  schedule_interval_minutes?: number | null;
  /**
   * 스케줄 조회에서 **처음 보는 fingerprint** 만 자동 분석한다.
   *
   * 비용이 나가는 경로다 — 이미 분석 이력이 있는 그룹은 다시 돌지 않고, 일일 분석
   * 한도(전역·정책별)가 그대로 적용된다. 화면 문구도 이 두 가지를 반드시 함께 적는다.
   */
  auto_analyze_new?: boolean;
}

export type PolicyCreate = PolicyBase;

export interface PolicyUpdate {
  loki_connection_id?: number | null;
  name?: string | null;
  description?: string | null;
  logql?: string | null;
  default_range_minutes?: number | null;
  max_lines?: number | null;
  exclusions?: string[] | null;
  max_samples_per_group?: number | null;
  allow_ai_analysis?: boolean | null;
  daily_analysis_limit?: number | null;
  /** 명시적 null 로 분모 쿼리를 지운다. 생략은 "변경 없음"이다. */
  baseline_query?: string | null;
  active?: boolean | null;
  schedule_enabled?: boolean | null;
  schedule_interval_minutes?: number | null;
  auto_analyze_new?: boolean | null;
}

export interface PolicyRead extends PolicyBase {
  id: number;
  active: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * 정책의 스케줄 설정을 화면 표시용으로 정규화한다.
 *
 * 백엔드가 아직 0004 를 배포하지 않았으면 필드 자체가 없다 — 그 경우 "스케줄 없음"으로
 * 접는다(배지를 감춘다). `schedule_enabled` 가 켜졌는데 주기가 비어 있는 상태도
 * 화면에서 구분해야 하므로 `intervalMinutes` 를 null 로 남긴다.
 */
export function policySchedule(policy: {
  schedule_enabled?: boolean;
  schedule_interval_minutes?: number | null;
  auto_analyze_new?: boolean;
}): { enabled: boolean; intervalMinutes: number | null; autoAnalyze: boolean } {
  return {
    enabled: policy.schedule_enabled === true,
    intervalMinutes:
      typeof policy.schedule_interval_minutes === 'number'
        ? policy.schedule_interval_minutes
        : null,
    autoAnalyze: policy.auto_analyze_new === true,
  };
}

/** 저장 전 실행 결과 미리보기. 잘못 쓴 쿼리가 정책으로 굳는 것을 막는다. */
export interface PolicyPreviewRequest {
  loki_connection_id: number;
  logql: string;
  range_minutes: number;
  limit: number;
  exclusions: string[];
}

/** `sample_lines` 는 화면 표시용이므로 이미 마스킹된 값이다. */
export interface PolicyPreviewResponse {
  fetched: number;
  dropped: number;
  truncated: boolean;
  warnings: FetchWarning[];
  sample_lines: string[];
}

// ================================================================ query runs

export interface QueryRunCreateRequest {
  range_start?: string | null;
  range_end?: string | null;
  /** 정책 max_lines 를 넘길 수 없다 — 상한은 서버가 강제한다. */
  limit?: number | null;
}

export interface QueryRunRead {
  id: number;
  policy_id: number;
  status: QueryRunStatus;
  started_at: string;
  finished_at?: string | null;
  range_start: string;
  range_end: string;
  fetched_count: number;
  dropped_count: number;
  warnings: FetchWarning[];
  error_message?: string | null;
  group_count: number;
  /** 수동 실행인지 스케줄인지. 백엔드가 아직 안 내려주면 `undefined` — 배지를 감춘다. */
  triggered_by?: TriggeredBy;
}

/**
 * `GET /api/policies/{id}/query-runs` — 정책의 실행 이력 (최신순 페이지네이션).
 *
 * 목록 항목은 단건 조회(`GET /api/query-runs/{id}`)와 같은 `QueryRunRead` 다.
 */
export interface QueryRunListResponse {
  total: number;
  limit: number;
  offset: number;
  items: QueryRunRead[];
}

// ============================================================== error groups

/**
 * 그룹 목록 항목. `analysis_status` 는 그룹 id 가 아니라 **fingerprint 기준**이다 —
 * 이전 조회에서 이미 분석된 그룹을 중복 요청(=중복 과금)하지 않게 하기 위해서다.
 */
export interface ErrorGroupSummary {
  id: number;
  query_run_id: number;
  fingerprint: string;
  service?: string | null;
  environment?: string | null;
  error_type?: string | null;
  normalized_message: string;
  count: number;
  first_seen: string;
  last_seen: string;
  analysis_status?: AnalysisJobStatus | null;
  latest_analysis_job_id?: number | null;
  latest_severity?: Severity | null;
}

export interface ErrorGroupListResponse {
  query_run_id: number;
  total: number;
  items: ErrorGroupSummary[];
}

/** 마스킹된 대표 로그. 원본은 저장하지 않으므로 여기에도 없다. */
export interface ErrorSampleRead {
  id: number;
  occurred_at: string;
  masked_log: string;
  labels: Record<string, string>;
  stacktrace?: string | null;
  masking_rule_version: string;
}

export interface AnalysisJobSummary {
  id: number;
  status: AnalysisJobStatus;
  provider: string;
  model: string;
  prompt_version: string;
  requested_at: string;
  completed_at?: string | null;
  severity?: Severity | null;
  summary?: string | null;
  /** 수동 트리거인지 스케줄의 자동 분석인지. 없으면 배지를 감춘다. */
  triggered_by?: TriggeredBy;
}

export interface ErrorGroupDetail extends ErrorGroupSummary {
  labels: Record<string, string>;
  top_stack_frame?: string | null;
  normalization_rule_version: string;
  samples: ErrorSampleRead[];
  /** 발생 추이 (metric 쿼리 기반) */
  trend: CountPoint[];
  /** `trend` 가 비어 있는 사유. 조회 실패와 "발생이 없었다"를 구분한다. */
  trend_warnings?: FetchWarning[];
  /** 같은 fingerprint 의 과거 분석 이력 (조회 회차를 넘어 조인) */
  analyses: AnalysisJobSummary[];
}

// ============================================================= analysis jobs

/** `llm_connection_id` 를 주지 않으면 기본 연결을 쓴다. */
export interface AnalysisJobCreateRequest {
  llm_connection_id?: number | null;
}

export interface UsageRecordRead {
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  /** 추정값. Decimal 이라 JSON 에서 문자열로 올 수 있다. */
  estimated_cost?: string | number | null;
  pricing_snapshot?: Record<string, unknown> | null;
  latency_ms?: number | null;
  status: UsageStatus;
  failure_reason?: string | null;
}

export interface AnalysisJobRead {
  id: number;
  error_group_id: number;
  llm_connection_id?: number | null;
  fingerprint: string;
  status: AnalysisJobStatus;
  /** 실행 시점 값으로 고정 저장된다 (연결 설정이 바뀌어도 이력은 유지). */
  provider: string;
  model: string;
  prompt_version: string;
  requested_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
  result?: AnalysisResultSchema | null;
  usage?: UsageRecordRead | null;
  triggered_by?: TriggeredBy;
}

/** 분석 시작은 멱등이다 — 진행 중인 작업이 있으면 `reused=true` 로 기존 작업을 반환한다. */
export interface AnalysisJobCreateResponse extends AnalysisJobRead {
  reused: boolean;
}

/**
 * app.schemas.api.AnalysisJobListItem — 목록 항목.
 *
 * 단건(`AnalysisJobRead`)과 **모양이 다르다.** 목록은 조회 회차와 무관하게 보여야 해서
 * 그룹 메타데이터를 값으로 싣는 대신 `result`·`usage` 는 싣지 않는다 — 심각도·요약은
 * `severity`/`summary` 로 평탄화되어 온다. 토큰·비용은 `GET /api/usage` 의 모델별
 * 집계에서 본다.
 */
export interface AnalysisJobListItem extends AnalysisJobSummary {
  error_group_id: number;
  fingerprint: string;
  llm_connection_id?: number | null;
  service?: string | null;
  environment?: string | null;
  error_type?: string | null;
  normalized_message?: string | null;
  error_message?: string | null;
}

/** app.schemas.api.AnalysisJobListResponse — 최신순 페이지네이션 목록. */
export interface AnalysisJobListResponse {
  total: number;
  limit: number;
  offset: number;
  items: AnalysisJobListItem[];
}

/**
 * `GET /api/analysis-jobs` 쿼리 파라미터 (Phase 6 에서 `q`·기간이 **추가**됐다).
 *
 * 추가는 additive 다 — 파라미터를 모르는 백엔드는 그냥 무시하고 전체를 준다. 그래서
 * 화면은 "검색이 먹지 않는다"를 오류로 다루지 않는다 (봉투가 같으므로 목록은 그대로 나온다).
 */
export interface AnalysisJobListParams {
  status?: AnalysisJobStatus;
  /** 부분 일치: 서비스·모델·normalized_message·fingerprint */
  q?: string;
  /** ISO datetime — `requested_at` 하한 */
  requested_from?: string;
  /** ISO datetime — `requested_at` 상한 */
  requested_to?: string;
  limit?: number;
  offset?: number;
}

// ================================================================= dashboard

export interface ServiceErrorCount {
  service?: string | null;
  count: number;
}

/**
 * 건수는 metric 쿼리 기반이며 로그 라인 수가 아니다.
 *
 * Phase 7 규칙:
 * - `series` 는 시각별 합산이다 (같은 timestamp 는 한 번만 온다).
 * - `group_count`·`unanalyzed_group_count` 는 회차 전체 COUNT 다 — `top_groups.length`
 *   (상위 N)를 지표 자리에 쓰지 않는다.
 * - `ingest_total`·`error_ratio` 는 정책에 `baseline_query` 가 있을 때만 온다.
 *   미설정·실패는 0 이 아니라 **null** 이며, 화면에는 `-` 로 표시한다.
 */
export interface DashboardOverviewResponse {
  policy_id?: number | null;
  query_run_id?: number | null;
  range_start: string;
  range_end: string;
  step_seconds: number;
  total_errors: number;
  series: CountPoint[];
  by_service: ServiceErrorCount[];
  top_groups: ErrorGroupSummary[];
  group_count?: number | null;
  unanalyzed_group_count?: number | null;
  ingest_total?: number | null;
  ingest_series: CountPoint[];
  error_ratio?: number | null;
  warnings: FetchWarning[];
}

export interface DashboardOverviewParams {
  policy_id?: number;
  query_run_id?: number;
  range_start?: string;
  range_end?: string;
  step_seconds?: number;
  top?: number;
}

// ------------------------------------------------- 통합 대시보드 (summary)

/**
 * `GET /api/dashboard/summary` — 정책 전체를 한 화면에서 훑기 위한 요약.
 *
 * `overview` 와 역할이 다르다. `overview` 는 **정책 하나**의 상세(추이·서비스별·상위 그룹)이고
 * 여기 있는 값은 **정책 목록**에 붙는 한 줄 요약이다. 정책이 스무 개가 되어도 "지금 무엇을
 * 봐야 하는가"를 카드 하나 크기로 답할 수 있어야 한다.
 */
export interface DashboardSummaryLastRun {
  id: number;
  started_at: string;
  status: QueryRunStatus;
  fetched_count: number;
  group_count: number;
  warnings: FetchWarning[];
}

/** 정책 단위 경고 (`{code, message}`). 조회 경고와 같은 코드 사전을 쓴다. */
export interface SummaryWarning {
  code: string;
  message: string;
}

export interface DashboardSummaryPolicy {
  policy_id: number;
  name: string;
  active: boolean;
  schedule_enabled: boolean;
  schedule_interval_minutes: number | null;
  last_run: DashboardSummaryLastRun | null;
  /**
   * 최근 **성공한** 조회의 그룹 중 fingerprint 분석 이력이 **전혀 없는** 수.
   *
   * 이 화면에서 가장 중요한 숫자다 — "새로 나타났는데 아무도 보지 않은 오류"의 개수이고,
   * 카드 정렬의 기본 기준이다. 그룹 id 가 아니라 fingerprint 기준이므로 이전 회차에서
   * 분석한 오류는 여기에 세지 않는다.
   */
  unanalyzed_group_count: number;
  /** `count_over_time` 최근 24h. metric 쿼리에 실패하면 **null** 이며 0 이 아니다. */
  total_errors_24h: number | null;
  /**
   * 카드 스파크라인용 24h 포인트 (step 3600).
   *
   * `total_errors_24h` 를 계산한 **같은 `count_over_time` 결과의 포인트를 재사용**한 값이다 —
   * 카드 하나마다 Loki 를 한 번 더 두드리지 않는다. metric 쿼리가 실패하면 빈 배열이고,
   * 그 사유는 `warnings` 에 남는다 (합계 쪽은 같은 이유로 `null` 이 된다).
   *
   * 백엔드가 아직 이 필드를 안 내려줄 수 있으므로 소비하는 쪽은 `undefined` 를 빈 배열과
   * 같게 다룬다 — 스파크라인 자리를 비우면 되고, 오류가 아니다.
   */
  series_24h?: SummarySeriesPoint[];
  warnings: SummaryWarning[];
}

/**
 * `series_24h` 의 한 점.
 *
 * 백엔드는 `CountPoint` 를 그대로 싣는다(`labels` 포함) — 같은 metric 결과를 재사용하기
 * 때문이다. 스파크라인이 쓰는 것은 `timestamp`·`value` 둘뿐이라 `labels` 는 **선택**으로
 * 둔다: 정책 전체 합계라 라벨이 비어 있어도 화면이 달라지지 않는다.
 */
export interface SummarySeriesPoint {
  /** ISO 8601 */
  timestamp: string;
  value: number;
  labels?: Record<string, string>;
}

export interface DashboardSummaryResponse {
  generated_at: string;
  policies: DashboardSummaryPolicy[];
}

// ------------------------------------------- 전체 오류 그룹 (통합 대시보드 하단)

/**
 * `GET /api/dashboard/error-groups` 의 항목.
 *
 * 전 활성 정책의 **최신 성공 query-run** 그룹을 모아 count desc, last_seen desc 로 준다.
 * 회차 하나짜리 목록(`/query-runs/{id}/error-groups`)과 달리 어느 정책에서 나온 그룹인지
 * 알아야 하므로 `policy_id`·`policy_name` 이 함께 온다.
 *
 * `latest_severity` 는 기존 fingerprint 조인 그대로다 — **LLM 추정** 값이고 발생량 기반
 * 지표가 아니다. 화면이 심각도로 배경색을 칠할 때도 배지를 반드시 병기한다
 * (색만으로 구분하면 색각 이상·흑백 출력에서 정보가 사라진다).
 */
export interface DashboardErrorGroupItem extends ErrorGroupSummary {
  policy_id: number;
  policy_name: string;
}

export interface DashboardErrorGroupsResponse {
  total: number;
  limit: number;
  offset: number;
  items: DashboardErrorGroupItem[];
}

// ===================================================================== usage

export interface UsageAggregate {
  provider: string;
  model: string;
  job_count: number;
  failure_count: number;
  input_tokens: number;
  output_tokens: number;
  /**
   * Decimal — JSON 에서 문자열로 올 수 있다. 추정값이다.
   * 단가표에 모델이 없으면 **null** 이다 (0 이 아니다 — 0 은 "쌌다"로 읽힌다).
   */
  estimated_cost: string | number | null;
  avg_latency_ms?: number | null;
}

/**
 * `group_by` 분해 축.
 *
 * 생략하면 기존 모델별 집계(`items`)만 온다 — 이 파라미터는 **additive** 라 모르는
 * 백엔드는 무시하고 기존 응답을 준다. 그래서 화면은 `buckets` 가 없는 상태를 실패가
 * 아니라 "아직 분해를 못 준다"로 다룬다.
 */
export type UsageGroupBy = 'day' | 'policy';

/**
 * `group_by` 분해의 한 칸.
 *
 * - `day`: `key` = `YYYY-MM-DD` (`app_settings.timezone` 로컬 날짜), `label` 동일
 * - `policy`: `key` = policy_id 문자열, `label` = 정책명. 정책 연결이 끊긴 작업은 `key="unknown"`
 *
 * `estimated_cost` 는 여기서도 **null 이 0 이 아니다** — 단가표에 없는 모델만 있는 칸은
 * 막대를 0 으로 그리지 말고 `-` 로 적는다.
 */
export interface UsageBucket {
  key: string;
  label: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: string | number | null;
  job_count: number;
  failure_count: number;
}

export interface UsageResponse {
  range_start: string;
  range_end: string;
  items: UsageAggregate[];
  total_jobs: number;
  total_input_tokens: number;
  total_output_tokens: number;
  /**
   * 추정 합계. 정산 근거가 아니다.
   * 비용을 계산할 수 있는 항목이 하나도 없으면 **null** 이며, 화면에는 `-` 로 표시한다.
   */
  total_estimated_cost: string | number | null;
  /**
   * `group_by` 를 준 요청에만 채워진다.
   *
   * 생략하면 **`null`** 이지 빈 배열이 아니다 — "분해를 요청하지 않았다"와 "분해했더니
   * 아무것도 없었다"는 다른 상태이고, 화면 문구도 갈린다. 파라미터를 모르는 옛 백엔드는
   * 키 자체를 안 보내므로 `undefined` 도 같이 받는다(둘 다 "분해 없음"이다).
   */
  buckets?: UsageBucket[] | null;
}

/** `GET /usage/daily-limit` 의 정책 한 줄 — **자체 한도를 가진 정책만** 온다. */
export interface PolicyDailyUsage {
  policy_id: number;
  name: string;
  limit: number;
  used: number;
}

/**
 * `GET /usage/daily-limit` — 오늘의 분석 한도 소진 게이지.
 *
 * "오늘"의 경계는 429 를 내는 한도 검사와 **같은 계산**이다
 * (`app_settings.timezone` 로컬 자정). 게이지와 429 가 다른 숫자를 보이면 안 된다.
 */
export interface DailyLimitResponse {
  /** 기준 로컬 날짜 "YYYY-MM-DD" 와 그 타임존 이름. */
  date: string;
  timezone: string;
  global_limit: number;
  global_used: number;
  policies: PolicyDailyUsage[];
}

export interface UsageParams {
  range_start?: string;
  range_end?: string;
  model?: string;
  provider?: string;
  /** 생략하면 기존 모델별 집계 그대로. */
  group_by?: UsageGroupBy;
}

// ================================================================== settings

/** app.app_settings.service.DESCRIPTIONS — 화이트리스트 밖 키는 404 다. */
export const SETTING_DAILY_ANALYSIS_LIMIT = 'daily_analysis_limit';
export const SETTING_MODEL_PRICING = 'model_pricing';
export const SETTING_SAMPLE_RETENTION_DAYS = 'sample_retention_days';
/** 일일 분석 한도의 리셋 기준 시간대. 프론트는 아직 읽기만 한다. */
export const SETTING_TIMEZONE = 'timezone';

export type SettingValue = Record<string, unknown> | unknown[] | string | number | boolean | null;

/** app.schemas.api.AppSettingRead — `value` 가 null 이면 `effective_value`(서버 기본값)를 쓴다. */
export interface AppSettingRead {
  key: string;
  value?: SettingValue;
  description?: string | null;
  updated_at?: string | null;
  effective_value?: SettingValue;
}

export interface AppSettingListResponse {
  items: AppSettingRead[];
}

/**
 * `model_pricing` 한 줄. 단가는 **1K 토큰당** 값이다 (`app/analysis/pricing.py`).
 *
 * 프로바이더 API 는 단가를 제공하지 않는다 — 이 표는 사람이 직접 채우는 것이 정답이고,
 * 표에 없는 모델의 추정 비용은 0 이 아니라 null 로 남는다.
 */
export interface ModelPricingEntry {
  input_per_1k?: number | null;
  output_per_1k?: number | null;
  currency?: string | null;
}

/** `{model: {input_per_1k, output_per_1k, currency}}` */
export type ModelPricingTable = Record<string, ModelPricingEntry>;

/** 단가표 값이 어떤 형태로 오든 dict 로만 받아들인다 (형식이 깨진 값은 빈 표로 본다). */
export function asModelPricingTable(value: SettingValue | undefined): ModelPricingTable {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return value as ModelPricingTable;
}
