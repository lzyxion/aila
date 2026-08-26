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
  active?: boolean | null;
}

export interface PolicyRead extends PolicyBase {
  id: number;
  active: boolean;
  created_at: string;
  updated_at: string;
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

// ================================================================= dashboard

export interface ServiceErrorCount {
  service?: string | null;
  count: number;
}

/** 건수는 metric 쿼리 기반이며 로그 라인 수가 아니다. */
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
}

export interface UsageParams {
  range_start?: string;
  range_end?: string;
  model?: string;
  provider?: string;
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
