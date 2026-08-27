/** TanStack Query 훅. 캐시 키는 여기 한 곳에서만 만든다. */

import { useEffect, useRef } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query';

import {
  analysisJobs,
  auth,
  dashboard,
  errorGroups,
  llmConnections,
  logSourceConnections,
  policies,
  queryRuns,
  settings,
  usage,
  users,
} from './endpoints';
import type {
  AnalysisJobCreateRequest,
  AnalysisJobListParams,
  AnalysisJobRead,
  AuthUser,
  DashboardOverviewParams,
  LoginRequest,
  LLMConnectionCreate,
  LLMConnectionUpdate,
  LLMModelListRequest,
  LogSourceConnectionCreate,
  LogSourceConnectionUpdate,
  ModelPricingEntry,
  ModelPricingTable,
  PolicyCreate,
  PolicyPreviewRequest,
  PolicyUpdate,
  QueryRunCreateRequest,
  UsageParams,
  UserCreateRequest,
  UserUpdateRequest,
} from './types';
import { asModelPricingTable, isActiveJobStatus, SETTING_MODEL_PRICING } from './types';

export const queryKeys = {
  authMe: ['auth', 'me'] as const,
  users: ['auth', 'users'] as const,
  logSourceConnections: ['log-source-connections'] as const,
  logSourceLabels: (id: number) => ['log-source-connections', id, 'labels'] as const,
  llmConnections: ['llm-connections'] as const,
  llmModels: (params: LLMModelListRequest) => ['llm-connections', 'models', params] as const,
  policies: ['policies'] as const,
  policy: (id: number) => ['policies', id] as const,
  policyQueryRuns: (id: number) => ['policies', id, 'query-runs'] as const,
  queryRun: (id: number) => ['query-runs', id] as const,
  errorGroups: (runId: number) => ['query-runs', runId, 'error-groups'] as const,
  errorGroup: (id: number) => ['error-groups', id] as const,
  analysisJobs: ['analysis-jobs'] as const,
  analysisJobList: (params: AnalysisJobListParams) => ['analysis-jobs', 'list', params] as const,
  analysisJob: (id: number) => ['analysis-jobs', id] as const,
  dashboard: (params: DashboardOverviewParams) => ['dashboard', 'overview', params] as const,
  dashboardSummary: ['dashboard', 'summary'] as const,
  dashboardErrorGroups: (params: { limit: number; offset: number }) =>
    ['dashboard', 'error-groups', params] as const,
  usage: (params: UsageParams) => ['usage', params] as const,
  dailyLimit: ['usage', 'daily-limit'] as const,
  settings: ['settings'] as const,
  setting: (key: string) => ['settings', key] as const,
};

// ---------------------------------------------------------------------- auth

/**
 * 세션 부트스트랩. 401(미로그인)·404(인증 미배포) 둘 다 **정상 경로**이므로
 * 재시도하지 않는다 — 그 구분은 `AuthProvider` 가 한다.
 */
export function useAuthMe() {
  return useQuery<AuthUser>({
    queryKey: queryKeys.authMe,
    queryFn: () => auth.me(),
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  });
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: LoginRequest) => auth.login(payload),
    onSuccess: (user) => {
      client.setQueryData(queryKeys.authMe, user);
      // 로그인 전 401 로 비어 있던 캐시를 전부 다시 읽는다.
      client.invalidateQueries();
    },
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => auth.logout(),
    onSuccess: () => {
      // 다른 계정으로 다시 들어올 수 있다 — 이전 세션이 본 데이터를 남기지 않는다.
      client.clear();
    },
  });
}

// -------------------------------------------------------------- 계정 관리

/**
 * 계정 목록 (admin 전용).
 *
 * viewer·미배포 백엔드에서는 403/404 가 난다 — 화면이 라우트 가드와 안내 폴백으로
 * 처리하므로 **재시도하지 않는다**.
 */
export function useUsers() {
  return useQuery({ queryKey: queryKeys.users, queryFn: () => users.list(), retry: false });
}

export function useCreateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: UserCreateRequest) => users.create(payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.users }),
  });
}

/**
 * 역할 변경 · 비활성/재활성 · 비밀번호 재설정.
 *
 * 마지막 admin 보호(409)는 서버 판정이므로 **성공 경로에서만** 목록을 갱신한다. 실패해도
 * 목록을 다시 읽으면 "아무 일도 없었는데 화면이 깜빡였다"가 되어 409 문구가 묻힌다.
 *
 * 자기 계정의 비밀번호를 바꾸면 **자기 세션도 무효화된다**(서버 계약) — 그 다음 요청의
 * 401 을 client 인터셉터가 잡아 로그인 화면으로 보낸다. 여기서 따로 처리하지 않는다.
 */
export function useUpdateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: UserUpdateRequest }) =>
      users.update(id, payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.users }),
  });
}

export function useDeactivateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => users.deactivate(id),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.users }),
  });
}

// --------------------------------------------------------------- connections

export function useLogSourceConnections() {
  return useQuery({
    queryKey: queryKeys.logSourceConnections,
    queryFn: () => logSourceConnections.list(),
  });
}

export function useLogSourceLabels(connectionId: number | null) {
  return useQuery({
    queryKey: queryKeys.logSourceLabels(connectionId ?? 0),
    queryFn: () => logSourceConnections.labels(connectionId as number),
    enabled: connectionId !== null,
    retry: false,
  });
}

export function useCreateLogSourceConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: LogSourceConnectionCreate) => logSourceConnections.create(payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.logSourceConnections }),
  });
}

export function useUpdateLogSourceConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: LogSourceConnectionUpdate }) =>
      logSourceConnections.update(id, payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.logSourceConnections }),
  });
}

/** 실제 삭제가 아니라 `active=false` 다 — 정책·조회 이력이 이 연결을 참조한다. */
export function useDeactivateLogSourceConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => logSourceConnections.deactivate(id),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.logSourceConnections }),
  });
}

export function useTestLogSourceConnection() {
  return useMutation({ mutationFn: logSourceConnections.test });
}

export function useLlmConnections() {
  return useQuery({ queryKey: queryKeys.llmConnections, queryFn: () => llmConnections.list() });
}

export function useCreateLlmConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: LLMConnectionCreate) => llmConnections.create(payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.llmConnections }),
  });
}

export function useUpdateLlmConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: LLMConnectionUpdate }) =>
      llmConnections.update(id, payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.llmConnections }),
  });
}

export function useDeactivateLlmConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => llmConnections.deactivate(id),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.llmConnections }),
  });
}

export function useTestLlmConnection() {
  return useMutation({ mutationFn: llmConnections.test });
}

/**
 * 프로바이더 모델 목록. **실패해도 화면은 자유 입력으로 폴백**하므로 재시도하지 않고,
 * 목록이 없다는 사실을 빠르게 화면에 알린다.
 *
 * `enabled` 를 호출부가 통제한다 — 키를 한 글자 칠 때마다 프로바이더를 두드리면 안 된다.
 */
export function useLlmModels(params: LLMModelListRequest, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.llmModels(params),
    queryFn: () => llmConnections.models(params),
    enabled,
    retry: false,
    staleTime: 5 * 60_000,
  });
}

// ------------------------------------------------------------------ policies

export function usePolicies() {
  return useQuery({ queryKey: queryKeys.policies, queryFn: () => policies.list() });
}

export function useCreatePolicy() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: PolicyCreate) => policies.create(payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.policies }),
  });
}

export function useUpdatePolicy() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: PolicyUpdate }) =>
      policies.update(id, payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.policies }),
  });
}

export function useDeactivatePolicy() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => policies.deactivate(id),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.policies }),
  });
}

/** 저장 전 미리보기. 캐시하지 않고 명시적으로 실행한다. */
export function usePreviewPolicy() {
  return useMutation({ mutationFn: (payload: PolicyPreviewRequest) => policies.preview(payload) });
}

export function useRunPolicy() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: QueryRunCreateRequest }) =>
      policies.run(id, payload),
    onSuccess: (_run, variables) => {
      client.invalidateQueries({ queryKey: ['dashboard'] });
      client.invalidateQueries({ queryKey: ['query-runs'] });
      // 방금 만든 실행이 그 정책의 이력 목록에 곧바로 보여야 한다.
      client.invalidateQueries({ queryKey: queryKeys.policyQueryRuns(variables.id) });
    },
  });
}

/**
 * 정책의 실행 이력. 백엔드에 아직 경로가 없을 수 있으므로(404/405/501) 재시도하지 않고
 * 화면이 안내 문구로 폴백한다.
 */
export function usePolicyQueryRuns(policyId: number | null, limit = 20) {
  return useQuery({
    queryKey: [...queryKeys.policyQueryRuns(policyId ?? 0), limit] as const,
    queryFn: () => policies.queryRuns(policyId as number, { limit }),
    enabled: policyId !== null,
    retry: false,
  });
}

// --------------------------------------------------------------- error groups

/** 조회 회차 단건. 정책 실행 이력에서 들어온 회차 화면이 쓴다. */
export function useQueryRun(runId: number | null) {
  return useQuery({
    queryKey: queryKeys.queryRun(runId ?? 0),
    queryFn: () => queryRuns.get(runId as number),
    enabled: runId !== null,
  });
}

export function useErrorGroups(runId: number | null) {
  return useQuery({
    queryKey: queryKeys.errorGroups(runId ?? 0),
    queryFn: () => queryRuns.errorGroups(runId as number, { limit: 100 }),
    enabled: runId !== null,
  });
}

export function useErrorGroup(groupId: number | null) {
  return useQuery({
    queryKey: queryKeys.errorGroup(groupId ?? 0),
    queryFn: () => errorGroups.get(groupId as number),
    enabled: groupId !== null,
  });
}

export function useStartAnalysis(groupId: number) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: AnalysisJobCreateRequest) => errorGroups.startAnalysis(groupId, payload),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.errorGroup(groupId) });
      client.invalidateQueries({ queryKey: queryKeys.analysisJobs });
    },
  });
}

/**
 * 분석 작업 폴링. `pending`/`running` 인 동안에만 재조회한다 —
 * 완료된 작업을 계속 두드릴 이유가 없다.
 */
export function useAnalysisJob(
  jobId: number | null,
  options?: Partial<UseQueryOptions<AnalysisJobRead>>,
) {
  return useQuery<AnalysisJobRead>({
    queryKey: queryKeys.analysisJob(jobId ?? 0),
    queryFn: () => analysisJobs.get(jobId as number),
    enabled: jobId !== null,
    refetchInterval: (query) =>
      isActiveJobStatus(query.state.data?.status) ? 2000 : false,
    ...options,
  });
}

/**
 * 폴링 + **완료 시 파생 화면 무효화**.
 *
 * `useAnalysisJob` 는 작업 단건만 갱신한다. 그런데 같은 화면의 "분석 이력"은 작업이
 * 아니라 **그룹 상세**(`error_groups/{id}.analyses`)에서 오고, 그 캐시는 작업이 끝나도
 * 아무도 건드리지 않았다 — 결과는 표시되는데 이력의 배지만 `분석 중` 스피너로 영원히
 * 남는 버그였다 (Phase 4 피드백 4번). 폴링이 끝나는 그 지점에서 한 번만 무효화한다.
 *
 * 같은 작업 id·같은 종료 상태로는 다시 무효화하지 않는다 (무효화 → 재조회 → 무효화 루프 차단).
 */
export function useAnalysisJobWithRefresh(jobId: number | null, groupId: number | null) {
  const client = useQueryClient();
  const query = useAnalysisJob(jobId);
  const status = query.data?.status;
  const settledRef = useRef<string | null>(null);

  useEffect(() => {
    if (jobId === null || !status || isActiveJobStatus(status)) return;
    const settledKey = `${jobId}:${status}`;
    if (settledRef.current === settledKey) return;
    settledRef.current = settledKey;
    if (groupId !== null) {
      client.invalidateQueries({ queryKey: queryKeys.errorGroup(groupId) });
    }
    client.invalidateQueries({ queryKey: queryKeys.analysisJobs });
    // 토큰·추정 비용은 작업이 끝나야 기록된다.
    client.invalidateQueries({ queryKey: ['usage'] });
  }, [client, groupId, jobId, status]);

  return query;
}

/**
 * 분석 이력 목록 (검색·기간·상태 필터 + 페이지네이션).
 *
 * 응답은 **봉투째** 온다 — `total` 이 있어야 "몇 건 중 몇 건"과 다음 페이지 버튼을 그릴 수
 * 있다. 진행 중 작업이 한 건이라도 있으면 폴링하고, 전부 끝나면 멈춘다.
 *
 * 필터 값이 캐시 키에 그대로 들어간다 — 이전 페이지로 돌아가면 재조회 없이 즉시 보인다.
 */
export function useAnalysisJobs(params: AnalysisJobListParams = {}) {
  return useQuery({
    queryKey: queryKeys.analysisJobList(params),
    queryFn: () => analysisJobs.list(params),
    retry: false,
    placeholderData: (previous) => previous,
    refetchInterval: (query) =>
      (query.state.data?.items ?? []).some((job) => isActiveJobStatus(job.status)) ? 3000 : false,
  });
}

// ------------------------------------------------------------------ dashboard

export function useDashboardOverview(params: DashboardOverviewParams) {
  return useQuery({
    queryKey: queryKeys.dashboard(params),
    queryFn: () => dashboard.overview(params),
  });
}

/**
 * 홈의 정책 카드 그리드.
 *
 * 백엔드에 아직 경로가 없을 수 있으므로(404/405/501) 재시도하지 않는다 —
 * 화면이 `GET /api/policies` 기반 축소 카드로 폴백한다.
 */
export function useDashboardSummary() {
  return useQuery({
    queryKey: queryKeys.dashboardSummary,
    queryFn: () => dashboard.summary(),
    retry: false,
  });
}

/**
 * 통합 대시보드 하단의 전체 오류 그룹 목록.
 *
 * 백엔드에 아직 경로가 없을 수 있으므로(404/405/501) 재시도하지 않는다 — 화면이 안내
 * 문구로 폴백하고, 정책 카드는 그대로 남는다.
 */
export function useDashboardErrorGroups(params: { limit: number; offset: number }) {
  return useQuery({
    queryKey: queryKeys.dashboardErrorGroups(params),
    queryFn: () => dashboard.errorGroups(params),
    retry: false,
    placeholderData: (previous) => previous,
  });
}

// ---------------------------------------------------------------------- usage

export function useUsage(params: UsageParams) {
  return useQuery({ queryKey: queryKeys.usage(params), queryFn: () => usage.get(params) });
}

/**
 * 오늘의 분석 한도 소진 게이지 (Phase 7).
 *
 * 백엔드에 아직 경로가 없을 수 있으므로(404/405/501) **재시도하지 않는다** — 화면은
 * 그 경우 게이지를 감춘다. 분석이 끝날 때마다 소진량이 바뀌므로 `['usage']` 무효화에
 * 같이 걸리도록 키를 `usage` 아래에 둔다.
 */
export function useDailyLimit() {
  return useQuery({
    queryKey: queryKeys.dailyLimit,
    queryFn: () => usage.dailyLimit(),
    retry: false,
    staleTime: 30_000,
  });
}

// ------------------------------------------------------------------- settings

/** 모델 단가표. 프로바이더 API 는 단가를 주지 않으므로 이 표는 사람이 채운다. */
export function useModelPricing() {
  return useQuery({
    queryKey: queryKeys.setting(SETTING_MODEL_PRICING),
    queryFn: () => settings.get(SETTING_MODEL_PRICING),
    retry: false,
  });
}

/**
 * 모델 하나의 단가를 등록·수정한다.
 *
 * `PUT` 은 키를 통째로 교체하므로 **기존 표에 병합**해서 보낸다 — 한 모델을 등록하다가
 * 다른 모델 단가를 지우면, 지워진 쪽의 추정 비용이 조용히 `-` 로 돌아간다.
 */
export function useUpsertModelPricing() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ model, entry }: { model: string; entry: ModelPricingEntry }) => {
      const current = await settings.get(SETTING_MODEL_PRICING).catch(() => null);
      const table: ModelPricingTable = {
        ...asModelPricingTable(current?.value ?? current?.effective_value),
        [model]: entry,
      };
      return settings.put(SETTING_MODEL_PRICING, table);
    },
    onSuccess: (data) => {
      client.setQueryData(queryKeys.setting(SETTING_MODEL_PRICING), data);
      client.invalidateQueries({ queryKey: queryKeys.settings });
    },
  });
}
