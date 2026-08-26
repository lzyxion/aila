/** TanStack Query 훅. 캐시 키는 여기 한 곳에서만 만든다. */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query';

import {
  analysisJobs,
  dashboard,
  errorGroups,
  llmConnections,
  lokiConnections,
  policies,
  queryRuns,
  usage,
} from './endpoints';
import type {
  AnalysisJobCreateRequest,
  AnalysisJobRead,
  DashboardOverviewParams,
  LLMConnectionCreate,
  LLMConnectionUpdate,
  LokiConnectionCreate,
  LokiConnectionUpdate,
  PolicyCreate,
  PolicyPreviewRequest,
  PolicyUpdate,
  QueryRunCreateRequest,
  UsageParams,
} from './types';
import { isActiveJobStatus } from './types';

export const queryKeys = {
  lokiConnections: ['loki-connections'] as const,
  lokiLabels: (id: number) => ['loki-connections', id, 'labels'] as const,
  llmConnections: ['llm-connections'] as const,
  policies: ['policies'] as const,
  policy: (id: number) => ['policies', id] as const,
  queryRun: (id: number) => ['query-runs', id] as const,
  errorGroups: (runId: number) => ['query-runs', runId, 'error-groups'] as const,
  errorGroup: (id: number) => ['error-groups', id] as const,
  analysisJobs: ['analysis-jobs'] as const,
  analysisJob: (id: number) => ['analysis-jobs', id] as const,
  dashboard: (params: DashboardOverviewParams) => ['dashboard', 'overview', params] as const,
  usage: (params: UsageParams) => ['usage', params] as const,
};

// --------------------------------------------------------------- connections

export function useLokiConnections() {
  return useQuery({ queryKey: queryKeys.lokiConnections, queryFn: () => lokiConnections.list() });
}

export function useLokiLabels(connectionId: number | null) {
  return useQuery({
    queryKey: queryKeys.lokiLabels(connectionId ?? 0),
    queryFn: () => lokiConnections.labels(connectionId as number),
    enabled: connectionId !== null,
    retry: false,
  });
}

export function useCreateLokiConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: LokiConnectionCreate) => lokiConnections.create(payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.lokiConnections }),
  });
}

export function useUpdateLokiConnection() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: LokiConnectionUpdate }) =>
      lokiConnections.update(id, payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.lokiConnections }),
  });
}

export function useTestLokiConnection() {
  return useMutation({ mutationFn: lokiConnections.test });
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
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['dashboard'] });
      client.invalidateQueries({ queryKey: ['query-runs'] });
    },
  });
}

// --------------------------------------------------------------- error groups

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

export function useAnalysisJobs() {
  return useQuery({
    queryKey: queryKeys.analysisJobs,
    queryFn: () => analysisJobs.list(),
    retry: false,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((job) => isActiveJobStatus(job.status)) ? 3000 : false,
  });
}

// ------------------------------------------------------------------ dashboard

export function useDashboardOverview(params: DashboardOverviewParams) {
  return useQuery({
    queryKey: queryKeys.dashboard(params),
    queryFn: () => dashboard.overview(params),
  });
}

// ---------------------------------------------------------------------- usage

export function useUsage(params: UsageParams) {
  return useQuery({ queryKey: queryKeys.usage(params), queryFn: () => usage.get(params) });
}
