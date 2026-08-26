/**
 * 라우트별 타입 지정 래퍼. 경로는 백엔드 각 모듈의 `router.py` 와 1:1 이다.
 * (`app.config.Settings.api_prefix` 기본값 `/api`)
 */

import { api } from './client';
import type {
  AnalysisJobCreateRequest,
  AnalysisJobCreateResponse,
  AnalysisJobListResponse,
  AnalysisJobRead,
  AnalysisJobStatus,
  AppSettingListResponse,
  AppSettingRead,
  ConnectionTestResponse,
  DashboardOverviewParams,
  DashboardOverviewResponse,
  ErrorGroupDetail,
  ErrorGroupListResponse,
  LabelValuesResponse,
  LLMConnectionCreate,
  LLMConnectionRead,
  LLMConnectionTestRequest,
  LLMConnectionUpdate,
  LLMModelListRequest,
  LLMModelListResponse,
  LokiConnectionCreate,
  LokiConnectionRead,
  LokiConnectionTestRequest,
  LokiConnectionUpdate,
  PolicyCreate,
  PolicyPreviewRequest,
  PolicyPreviewResponse,
  PolicyRead,
  PolicyUpdate,
  QueryRunCreateRequest,
  QueryRunListResponse,
  QueryRunRead,
  SettingValue,
  UsageParams,
  UsageResponse,
} from './types';

const P = '/api';

// --------------------------------------------------------- loki connections

export const lokiConnections = {
  list: () => api.get<LokiConnectionRead[]>(`${P}/loki-connections`),
  get: (id: number) => api.get<LokiConnectionRead>(`${P}/loki-connections/${id}`),
  create: (payload: LokiConnectionCreate) =>
    api.post<LokiConnectionRead>(`${P}/loki-connections`, payload),
  update: (id: number, payload: LokiConnectionUpdate) =>
    api.patch<LokiConnectionRead>(`${P}/loki-connections/${id}`, payload),
  /** 실제 삭제가 아니라 active=false 비활성화다. */
  deactivate: (id: number) => api.delete(`${P}/loki-connections/${id}`),
  test: (payload: LokiConnectionTestRequest) =>
    api.post<ConnectionTestResponse>(`${P}/loki-connections/test`, payload),
  labels: (id: number) => api.get<LabelValuesResponse>(`${P}/loki-connections/${id}/labels`),
};

// ---------------------------------------------------------- llm connections

export const llmConnections = {
  list: () => api.get<LLMConnectionRead[]>(`${P}/llm-connections`),
  get: (id: number) => api.get<LLMConnectionRead>(`${P}/llm-connections/${id}`),
  create: (payload: LLMConnectionCreate) =>
    api.post<LLMConnectionRead>(`${P}/llm-connections`, payload),
  update: (id: number, payload: LLMConnectionUpdate) =>
    api.patch<LLMConnectionRead>(`${P}/llm-connections/${id}`, payload),
  deactivate: (id: number) => api.delete(`${P}/llm-connections/${id}`),
  /** 연결 테스트도 실제 과금 호출이다 — 백엔드가 최소 토큰으로 보낸다. */
  test: (payload: LLMConnectionTestRequest) =>
    api.post<ConnectionTestResponse>(`${P}/llm-connections/test`, payload),
  /**
   * 프로바이더가 제공하는 모델 목록. 저장된 연결(`connection_id`)이나 아직 저장하지 않은
   * 입력값(`api_key`/`base_url`) 중 하나로 조회한다.
   *
   * **조회지만 POST 다** — `api_key` 를 쿼리스트링에 실으면 평문 키가 서버 액세스
   * 로그·프록시 로그·브라우저 히스토리에 남는다. 비밀은 바디로만 보낸다.
   *
   * 실패는 화면을 막지 않는다 — 호출부가 **자유 입력으로 폴백**한다.
   */
  models: (payload: LLMModelListRequest) =>
    api.post<LLMModelListResponse>(`${P}/llm-connections/models`, {
      provider: payload.provider ?? undefined,
      connection_id: payload.connection_id ?? undefined,
      api_key: payload.api_key ?? undefined,
      base_url: payload.base_url ?? undefined,
    }),
};

// ---------------------------------------------------------------- policies

export const policies = {
  list: (active?: boolean) =>
    api.get<PolicyRead[]>(`${P}/policies`, { query: { active } }),
  get: (id: number) => api.get<PolicyRead>(`${P}/policies/${id}`),
  create: (payload: PolicyCreate) => api.post<PolicyRead>(`${P}/policies`, payload),
  update: (id: number, payload: PolicyUpdate) =>
    api.patch<PolicyRead>(`${P}/policies/${id}`, payload),
  deactivate: (id: number) => api.delete(`${P}/policies/${id}`),
  /** 저장 전 실행 결과 미리보기. sample_lines 는 이미 마스킹된 값이다. */
  preview: (payload: PolicyPreviewRequest) =>
    api.post<PolicyPreviewResponse>(`${P}/policies/preview`, payload),
  run: (id: number, payload: QueryRunCreateRequest) =>
    api.post<QueryRunRead>(`${P}/policies/${id}/query-runs`, payload),
  /** 정책의 실행 이력 (최신순). 봉투 `{total, limit, offset, items}` 로 온다. */
  queryRuns: (id: number, params?: { limit?: number; offset?: number }) =>
    api.get<QueryRunListResponse>(`${P}/policies/${id}/query-runs`, {
      query: { limit: params?.limit, offset: params?.offset },
    }),
};

export const queryRuns = {
  get: (id: number) => api.get<QueryRunRead>(`${P}/query-runs/${id}`),
  errorGroups: (id: number, params?: { limit?: number; offset?: number }) =>
    api.get<ErrorGroupListResponse>(`${P}/query-runs/${id}/error-groups`, {
      query: { limit: params?.limit, offset: params?.offset },
    }),
};

// ------------------------------------------------------------- error groups

export const errorGroups = {
  get: (id: number) => api.get<ErrorGroupDetail>(`${P}/error-groups/${id}`),
  startAnalysis: (id: number, payload: AnalysisJobCreateRequest) =>
    api.post<AnalysisJobCreateResponse>(`${P}/error-groups/${id}/analysis-jobs`, payload),
};

// ------------------------------------------------------------ analysis jobs

export const analysisJobs = {
  /**
   * 분석 실행 목록 (최신순·페이지네이션).
   *
   * 응답은 배열이 아니라 `{total, limit, offset, items}` 봉투이고, 항목은 단건 조회와
   * 모양이 다르다(`AnalysisJobListItem` — `result`·`usage` 가 없다). 화면은 항목만
   * 쓰므로 여기서 벗겨서 넘긴다.
   */
  list: (params?: { status?: AnalysisJobStatus; limit?: number; offset?: number }) =>
    api
      .get<AnalysisJobListResponse>(`${P}/analysis-jobs`, { query: { ...params } })
      .then((response) => response.items),
  get: (id: number) => api.get<AnalysisJobRead>(`${P}/analysis-jobs/${id}`),
  /** 보고서는 저장하지 않고 요청 시점에 Markdown 으로 렌더링된다. */
  report: (id: number) =>
    api.get<string>(`${P}/analysis-jobs/${id}/report`, { parse: 'text' }),
};

// ---------------------------------------------------------------- dashboard

export const dashboard = {
  overview: (params: DashboardOverviewParams) =>
    api.get<DashboardOverviewResponse>(`${P}/dashboard/overview`, {
      query: { ...params },
    }),
};

// -------------------------------------------------------------------- usage

export const usage = {
  get: (params: UsageParams) => api.get<UsageResponse>(`${P}/usage`, { query: { ...params } }),
};

// ----------------------------------------------------------------- settings

/**
 * 예약 설정 3 종(`daily_analysis_limit`·`model_pricing`·`sample_retention_days`).
 *
 * `PUT` 은 키 하나를 **통째로 교체**한다 — 단가표에 모델을 추가할 때는 호출부가 기존
 * 표를 읽어 병합한 값을 보내야 한다 (그렇지 않으면 다른 모델 단가가 사라진다).
 */
export const settings = {
  list: () => api.get<AppSettingListResponse>(`${P}/settings`),
  get: (key: string) => api.get<AppSettingRead>(`${P}/settings/${key}`),
  put: (key: string, value: SettingValue) =>
    api.put<AppSettingRead>(`${P}/settings/${key}`, { value }),
};
