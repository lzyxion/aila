/**
 * 라우트별 타입 지정 래퍼. 경로는 백엔드 각 모듈의 `router.py` 와 1:1 이다.
 * (`app.config.Settings.api_prefix` 기본값 `/api`)
 */

import { api } from './client';
import type {
  AnalysisJobCreateRequest,
  AnalysisJobCreateResponse,
  AnalysisJobRead,
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
   * 분석 실행 목록.
   *
   * **주의: 이 엔드포인트는 백엔드 계약(API 초안·라우터 스켈레톤)에 없다.**
   * 설계 문서 "분석 이력·사용량" 화면이 실행 목록을 요구하는데 API 목록에 빠져 있다.
   * mock 모드에서는 동작하고, 라이브 모드에서는 404/501 이 나므로 화면이 안내로 처리한다.
   */
  list: () => api.get<AnalysisJobRead[]>(`${P}/analysis-jobs`),
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
