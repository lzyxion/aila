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
  QueryRunRead,
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
