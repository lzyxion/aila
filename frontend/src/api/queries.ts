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
  dashboard,
  errorGroups,
  llmConnections,
  lokiConnections,
  policies,
  queryRuns,
  settings,
  usage,
} from './endpoints';
import type {
  AnalysisJobCreateRequest,
  AnalysisJobRead,
  DashboardOverviewParams,
  LLMConnectionCreate,
  LLMConnectionUpdate,
  LLMModelListRequest,
  LokiConnectionCreate,
  LokiConnectionUpdate,
  ModelPricingEntry,
  ModelPricingTable,
  PolicyCreate,
  PolicyPreviewRequest,
  PolicyUpdate,
  QueryRunCreateRequest,
  UsageParams,
} from './types';
import { asModelPricingTable, isActiveJobStatus, SETTING_MODEL_PRICING } from './types';

export const queryKeys = {
  lokiConnections: ['loki-connections'] as const,
  lokiLabels: (id: number) => ['loki-connections', id, 'labels'] as const,
  llmConnections: ['llm-connections'] as const,
  llmModels: (params: LLMModelListRequest) => ['llm-connections', 'models', params] as const,
  policies: ['policies'] as const,
  policy: (id: number) => ['policies', id] as const,
  policyQueryRuns: (id: number) => ['policies', id, 'query-runs'] as const,
  queryRun: (id: number) => ['query-runs', id] as const,
  errorGroups: (runId: number) => ['query-runs', runId, 'error-groups'] as const,
  errorGroup: (id: number) => ['error-groups', id] as const,
  analysisJobs: ['analysis-jobs'] as const,
  analysisJob: (id: number) => ['analysis-jobs', id] as const,
  dashboard: (params: DashboardOverviewParams) => ['dashboard', 'overview', params] as const,
  usage: (params: UsageParams) => ['usage', params] as const,
  settings: ['settings'] as const,
  setting: (key: string) => ['settings', key] as const,
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
