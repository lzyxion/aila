/**
 * 라우트별 타입 지정 래퍼. 경로는 백엔드 각 모듈의 `router.py` 와 1:1 이다.
 * (`app.config.Settings.api_prefix` 기본값 `/api`)
 */

import { api } from './client';
import type {
  AnalysisJobCreateRequest,
  AnalysisJobCreateResponse,
  AnalysisJobListParams,
  AnalysisJobListResponse,
  AnalysisJobRead,
  AppSettingListResponse,
  AppSettingRead,
  AuthUser,
  ConnectionTestResponse,
  DailyLimitResponse,
  DashboardErrorGroupsResponse,
  DashboardOverviewParams,
  DashboardOverviewResponse,
  DashboardSummaryResponse,
  LoginRequest,
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
  UserCreateRequest,
  UserCreateResponse,
  UserListResponse,
  UserRead,
  UserUpdateRequest,
} from './types';

const P = '/api';

// -------------------------------------------------------------------- auth

/**
 * 세션 인증. `/api/**` 는 auth 라우트와 `/health` 를 빼고 전부 인증이 필요하다.
 *
 * 토큰은 응답 본문에 오지 않는다 — 로그인이 성공하면 **httpOnly 세션 쿠키**(SameSite=Lax)
 * 가 붙고 이후 요청은 브라우저가 알아서 싣는다. 자바스크립트가 읽을 수 있는 자리에
 * 토큰을 두면 XSS 하나로 세션이 통째로 넘어간다.
 */
export const auth = {
  login: (payload: LoginRequest) => api.post<AuthUser>(`${P}/auth/login`, payload),
  /** 204 — 본문이 없다. */
  logout: () => api.post<void>(`${P}/auth/logout`, undefined, { parse: 'void' }),
  /** 미인증이면 401 이다. 부트스트랩 경로라 그 401 은 오류가 아니라 "로그인 안 함"이다. */
  me: () => api.get<AuthUser>(`${P}/auth/me`),
};

/**
 * 계정 관리 — **admin 전용**이다. viewer 는 GET 도 403 을 받을 수 있으므로 화면이
 * 라우트 자체를 가린다 (판정은 여전히 서버가 한다).
 *
 * 세 가지 보호가 서버에만 있다: 마지막 남은 active admin 의 강등·비활성(409), 자기 자신
 * 비활성(409), `active=false`·비밀번호 변경 시 그 계정 세션 전부 무효화. 화면은 409 의
 * `detail` 을 **그대로** 보여준다 — 문구를 프런트가 다시 쓰면 서버가 규칙을 바꿨을 때
 * 화면만 옛말을 하게 된다.
 */
export const users = {
  list: () => api.get<UserListResponse>(`${P}/auth/users`),
  create: (payload: UserCreateRequest) =>
    api.post<UserCreateResponse>(`${P}/auth/users`, payload),
  update: (id: number, payload: UserUpdateRequest) =>
    api.patch<UserRead>(`${P}/auth/users/${id}`, payload),
  /** 실제 삭제가 아니라 `active=false` + 세션 무효화다. 자기 자신은 409. */
  deactivate: (id: number) => api.delete(`${P}/auth/users/${id}`),
};

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
   * 모양이 다르다(`AnalysisJobListItem` — `result`·`usage` 가 없다). **봉투째 넘긴다** —
   * 페이지네이션 화면이 `total` 을 알아야 "몇 건 중 몇 건"을 쓸 수 있고, 여기서 벗기면
   * 그 값이 화면에 닿을 방법이 없어진다.
   *
   * `q`·`requested_from`·`requested_to` 는 additive 파라미터다 — 모르는 백엔드는 무시하고
   * 전체를 준다(오류가 아니다).
   */
  list: (params?: AnalysisJobListParams) =>
    api.get<AnalysisJobListResponse>(`${P}/analysis-jobs`, { query: { ...params } }),
  get: (id: number) => api.get<AnalysisJobRead>(`${P}/analysis-jobs/${id}`),
  /** 보고서는 저장하지 않고 요청 시점에 Markdown 으로 렌더링된다. */
  report: (id: number) =>
    api.get<string>(`${P}/analysis-jobs/${id}/report`, { parse: 'text' }),
};

// ---------------------------------------------------------------- dashboard

export const dashboard = {
  /** 정책 하나의 상세 뷰 (`/dashboard/:policyId`). 추이·서비스별·상위 그룹. */
  overview: (params: DashboardOverviewParams) =>
    api.get<DashboardOverviewResponse>(`${P}/dashboard/overview`, {
      query: { ...params },
    }),
  /**
   * 홈의 정책 카드 그리드. 정책 하나당 한 줄 요약이라 `overview` 를 정책 수만큼
   * 부르는 것과 다르다 — 카드에 필요한 값만 서버가 한 번에 모아 준다.
   *
   * 백엔드에 아직 이 경로가 없으면 호출부가 `GET /api/policies` 기반 축소 카드로
   * 폴백한다 (`isEndpointMissing`).
   */
  summary: () => api.get<DashboardSummaryResponse>(`${P}/dashboard/summary`),
  /**
   * 통합 대시보드 하단의 **전체 오류 그룹** 목록.
   *
   * 전 활성 정책의 최신 성공 query-run 그룹을 모아 count desc, last_seen desc 로 준다 —
   * 정책 카드를 하나씩 열어 보지 않아도 "지금 가장 많이 터지는 오류"가 한 화면에 나온다.
   *
   * 백엔드에 아직 경로가 없으면 호출부가 안내 폴백으로 물러난다 (`isEndpointMissing`).
   */
  errorGroups: (params?: { limit?: number; offset?: number }) =>
    api.get<DashboardErrorGroupsResponse>(`${P}/dashboard/error-groups`, {
      query: { limit: params?.limit, offset: params?.offset },
    }),
};

// -------------------------------------------------------------------- usage

export const usage = {
  get: (params: UsageParams) => api.get<UsageResponse>(`${P}/usage`, { query: { ...params } }),
  /**
   * 오늘의 분석 한도 소진 게이지 (Phase 7).
   *
   * "오늘"의 경계는 429 를 내는 한도 검사와 **같은 계산**이다(`app_settings.timezone` 의
   * 로컬 자정) — 게이지와 429 가 다른 숫자를 보이면 게이지를 믿을 수 없다.
   *
   * 백엔드에 아직 경로가 없으면 호출부가 게이지를 **감춘다** (`isEndpointMissing`) —
   * 한도를 모르는 상태를 "0/0" 으로 그리면 "다 썼다"로 읽힌다.
   */
  dailyLimit: () => api.get<DailyLimitResponse>(`${P}/usage/daily-limit`),
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
