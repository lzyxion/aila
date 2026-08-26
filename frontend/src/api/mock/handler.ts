/**
 * mock 라우터. `VITE_USE_MOCK=true` 일 때 `apiRequest` 가 여기로 들어온다.
 *
 * 목적은 하나다 — 백엔드 다수 엔드포인트가 아직 501 인 Phase 1 에서 **화면을 단독으로
 * 끝까지 확인**할 수 있게 하는 것. 그래서 CRUD 는 메모리에서 실제로 변하고, 분석 작업은
 * pending → running → succeeded 로 시간에 따라 진행해 폴링 화면이 실제처럼 움직인다.
 *
 * 계약을 흉내 내는 부분(중요):
 * - 분석 시작은 **멱등**이다. 같은 그룹에 진행 중 작업이 있으면 `reused=true` 로 기존 작업 반환.
 * - `analysis_status` 는 그룹 id 가 아니라 **fingerprint 기준**으로 채운다.
 * - 응답에 실리는 로그는 전부 마스킹된 값이고 secret 은 절대 평문으로 나가지 않는다.
 * - 건수·추이는 metric 시리즈에서 오며 로그 라인 수를 세지 않는다.
 */

import { ApiError, type HttpMethod, type RequestOptions } from '../client';
import type {
  AnalysisJobCreateRequest,
  AnalysisJobCreateResponse,
  AnalysisJobListItem,
  AnalysisJobListResponse,
  AnalysisJobRead,
  AnalysisJobSummary,
  ConnectionTestResponse,
  DashboardOverviewResponse,
  ErrorGroupDetail,
  ErrorGroupListResponse,
  ErrorGroupSummary,
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
  ServiceErrorCount,
  UsageAggregate,
  UsageResponse,
} from '../types';
import {
  analysisJobSeed,
  analysisResultByFingerprint,
  buildGroupDetail,
  fallbackAnalysisResult,
  groupSeeds,
  iso,
  llmConnectionSeed,
  lokiConnectionSeed,
  makeSeries,
  policySeed,
  seriesShapes,
} from './fixtures';
import { renderReportMarkdown } from '../../lib/report';

const LATENCY = Number(import.meta.env.VITE_MOCK_LATENCY_MS ?? 250);

// ------------------------------------------------------------------- state

interface MockState {
  lokiConnections: LokiConnectionRead[];
  llmConnections: LLMConnectionRead[];
  policies: PolicyRead[];
  queryRuns: QueryRunRead[];
  /** job.id -> job */
  jobs: Map<number, AnalysisJobRead>;
  /** job.id -> 시뮬레이션 시작 시각(ms). 진행 상태를 시간으로 계산한다. */
  jobStartedAtMs: Map<number, number>;
  nextId: number;
}

const state: MockState = {
  lokiConnections: structuredClone(lokiConnectionSeed),
  llmConnections: structuredClone(llmConnectionSeed),
  policies: structuredClone(policySeed),
  queryRuns: [
    {
      id: 5001,
      policy_id: 1,
      status: 'succeeded',
      started_at: iso(-62),
      finished_at: iso(-61),
      range_start: iso(-60),
      range_end: iso(0),
      fetched_count: 903,
      dropped_count: 47,
      warnings: [
        {
          code: 'parse_error',
          message: '`| json` 파싱에 실패한 라인이 있습니다. `| __error__=""` 처리를 검토하십시오.',
          count: 47,
        },
      ],
      error_message: null,
      group_count: groupSeeds.length,
    },
  ],
  jobs: new Map(analysisJobSeed.map((job) => [job.id, structuredClone(job)])),
  jobStartedAtMs: new Map(),
  nextId: 9100,
};

const LATEST_RUN_ID = 5001;

function delay<T>(value: T): Promise<T> {
  if (LATENCY <= 0) return Promise.resolve(value);
  return new Promise((resolve) => setTimeout(() => resolve(value), LATENCY));
}

function nextId(): number {
  state.nextId += 1;
  return state.nextId;
}

function nowIso(): string {
  return new Date().toISOString();
}

// -------------------------------------------------- 분석 작업 상태 시뮬레이션

/**
 * 진행 중 작업을 시간에 따라 전이시킨다. 실제 백엔드는 BackgroundTasks 가 하는 일이고,
 * 화면 입장에서는 "POST 후 GET 으로 폴링" 이라는 모양만 같으면 된다.
 */
function advanceJob(job: AnalysisJobRead): AnalysisJobRead {
  if (job.status !== 'pending' && job.status !== 'running') return job;
  const startedMs = state.jobStartedAtMs.get(job.id);
  if (startedMs === undefined) return job;
  const elapsed = Date.now() - startedMs;

  if (elapsed < 1500) {
    job.status = 'pending';
    return job;
  }
  if (elapsed < 6000) {
    job.status = 'running';
    job.started_at = job.started_at ?? nowIso();
    return job;
  }

  job.status = 'succeeded';
  job.completed_at = nowIso();
  job.result = analysisResultByFingerprint[job.fingerprint] ?? fallbackAnalysisResult;
  const inputTokens = 2400 + (job.id % 7) * 130;
  const outputTokens = 560 + (job.id % 5) * 45;
  const pricing =
    job.provider === 'anthropic'
      ? { input: 3.0, output: 15.0 }
      : { input: 1.25, output: 10.0 };
  const cost = (inputTokens / 1e6) * pricing.input + (outputTokens / 1e6) * pricing.output;
  job.usage = {
    provider: job.provider,
    model: job.model,
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    estimated_cost: cost.toFixed(4),
    pricing_snapshot: {
      input_per_mtok: pricing.input.toFixed(2),
      output_per_mtok: pricing.output.toFixed(2),
      currency: 'USD',
    },
    latency_ms: Math.round(elapsed),
    status: 'succeeded',
    failure_reason: null,
  };
  return job;
}

function allJobs(): AnalysisJobRead[] {
  return [...state.jobs.values()].map(advanceJob);
}

/** fingerprint 기준 최신 작업 — 그룹의 `analysis_status` 는 이 값에서 온다. */
function latestJobForFingerprint(fingerprint: string): AnalysisJobRead | undefined {
  return allJobs()
    .filter((job) => job.fingerprint === fingerprint)
    .sort((a, b) => Date.parse(b.requested_at) - Date.parse(a.requested_at))[0];
}

function toJobSummary(job: AnalysisJobRead): AnalysisJobSummary {
  return {
    id: job.id,
    status: job.status,
    provider: job.provider,
    model: job.model,
    prompt_version: job.prompt_version,
    requested_at: job.requested_at,
    completed_at: job.completed_at ?? null,
    severity: job.result?.severity ?? null,
    summary: job.result?.summary ?? null,
  };
}

/** 목록 항목. 조회 회차와 무관하게 보여야 하므로 그룹 메타데이터를 값으로 함께 싣는다. */
function toJobListItem(job: AnalysisJobRead): AnalysisJobListItem {
  const seed = groupSeeds.find((item) => item.id === job.error_group_id);
  return {
    ...toJobSummary(job),
    error_group_id: job.error_group_id,
    fingerprint: job.fingerprint,
    llm_connection_id: job.llm_connection_id ?? null,
    service: seed?.service ?? null,
    environment: seed?.environment ?? null,
    error_type: seed?.error_type ?? null,
    normalized_message: seed?.normalized_message ?? null,
    error_message: job.error_message ?? null,
  };
}

// ------------------------------------------------------------ 그룹 조립

function groupSummary(seedIndex: number, queryRunId: number): ErrorGroupSummary {
  const detail = buildGroupDetail(groupSeeds[seedIndex], queryRunId);
  const latest = latestJobForFingerprint(detail.fingerprint);
  return {
    id: detail.id,
    query_run_id: detail.query_run_id,
    fingerprint: detail.fingerprint,
    service: detail.service,
    environment: detail.environment,
    error_type: detail.error_type,
    normalized_message: detail.normalized_message,
    count: detail.count,
    first_seen: detail.first_seen,
    last_seen: detail.last_seen,
    analysis_status: latest?.status ?? null,
    latest_analysis_job_id: latest?.id ?? null,
    latest_severity: latest?.result?.severity ?? null,
  };
}

function groupDetail(groupId: number): ErrorGroupDetail {
  const index = groupSeeds.findIndex((seed) => seed.id === groupId);
  if (index < 0) throw new ApiError(404, `오류 그룹 ${groupId} 을(를) 찾을 수 없습니다.`);
  const detail = buildGroupDetail(groupSeeds[index], LATEST_RUN_ID);
  const summary = groupSummary(index, LATEST_RUN_ID);
  detail.analysis_status = summary.analysis_status;
  detail.latest_analysis_job_id = summary.latest_analysis_job_id;
  detail.latest_severity = summary.latest_severity;
  detail.analyses = allJobs()
    .filter((job) => job.fingerprint === detail.fingerprint)
    .sort((a, b) => Date.parse(b.requested_at) - Date.parse(a.requested_at))
    .map(toJobSummary);
  return detail;
}

// ------------------------------------------------------------------ 라우팅

interface Ctx {
  body: any;
  query: Record<string, string | number | boolean | undefined | null>;
}

type Handler = (params: string[], ctx: Ctx) => unknown;

interface Route {
  method: HttpMethod;
  pattern: RegExp;
  handler: Handler;
}

const routes: Route[] = [];

function route(method: HttpMethod, pattern: RegExp, handler: Handler): void {
  routes.push({ method, pattern, handler });
}

// --- loki connections -------------------------------------------------------

route('GET', /^\/api\/loki-connections$/, () => state.lokiConnections);

route('POST', /^\/api\/loki-connections$/, (_p, { body }) => {
  const payload = body as LokiConnectionCreate;
  const created: LokiConnectionRead = {
    id: nextId(),
    name: payload.name,
    source_type: payload.source_type ?? 'loki',
    base_url: payload.base_url,
    auth_type: payload.auth_type ?? 'none',
    label_mapping: payload.label_mapping ?? {},
    active: payload.active ?? true,
    // 평문 secret 은 저장도 반환도 하지 않는다 — 존재 여부만 남긴다.
    has_secret: Boolean(payload.secret),
    created_at: nowIso(),
    updated_at: nowIso(),
  };
  state.lokiConnections.push(created);
  return created;
});

route('POST', /^\/api\/loki-connections\/test$/, (_p, { body }) => {
  const payload = body as LokiConnectionTestRequest;
  const target =
    payload.base_url ??
    state.lokiConnections.find((c) => c.id === payload.connection_id)?.base_url ??
    '';
  const ok = /^https?:\/\//.test(target);
  const response: ConnectionTestResponse = ok
    ? {
        ok: true,
        message: 'Loki 에 연결했습니다.',
        latency_ms: 42,
        details: { version: '3.5.1', supports_count: true, supports_label_discovery: true },
      }
    : {
        ok: false,
        message: 'base URL 이 http(s) 로 시작해야 합니다.',
        latency_ms: null,
        details: {},
      };
  return response;
});

route('GET', /^\/api\/loki-connections\/(\d+)$/, ([id]) => {
  const found = state.lokiConnections.find((c) => c.id === Number(id));
  if (!found) throw new ApiError(404, `연결 ${id} 을(를) 찾을 수 없습니다.`);
  return found;
});

route('PATCH', /^\/api\/loki-connections\/(\d+)$/, ([id], { body }) => {
  const found = state.lokiConnections.find((c) => c.id === Number(id));
  if (!found) throw new ApiError(404, `연결 ${id} 을(를) 찾을 수 없습니다.`);
  const payload = body as LokiConnectionUpdate;
  if (payload.name != null) found.name = payload.name;
  if (payload.base_url != null) found.base_url = payload.base_url;
  if (payload.auth_type != null) found.auth_type = payload.auth_type;
  if (payload.label_mapping != null) found.label_mapping = payload.label_mapping;
  if (payload.active != null) found.active = payload.active;
  if (payload.secret != null) found.has_secret = payload.secret.length > 0;
  found.updated_at = nowIso();
  return found;
});

route('DELETE', /^\/api\/loki-connections\/(\d+)$/, ([id]) => {
  const found = state.lokiConnections.find((c) => c.id === Number(id));
  if (found) found.active = false;
  return undefined;
});

route('GET', /^\/api\/loki-connections\/(\d+)\/labels$/, () => {
  const response: LabelValuesResponse = {
    labels: ['service', 'environment', 'level', 'release', 'pod', 'namespace'],
    values: {
      service: ['payment-api', 'order-api', 'auth-api', 'user-api', 'notification-api'],
      environment: ['staging', 'production'],
      level: ['ERROR', 'WARN', 'FATAL', 'INFO'],
    },
    supports_label_discovery: true,
  };
  return response;
});

// --- llm connections --------------------------------------------------------

route('GET', /^\/api\/llm-connections$/, () => state.llmConnections);

route('POST', /^\/api\/llm-connections$/, (_p, { body }) => {
  const payload = body as LLMConnectionCreate;
  if (payload.is_default) {
    state.llmConnections.forEach((c) => {
      c.is_default = false;
    });
  }
  const created: LLMConnectionRead = {
    id: nextId(),
    name: payload.name,
    provider: payload.provider,
    model: payload.model,
    base_url: payload.base_url ?? null,
    is_default: payload.is_default ?? false,
    active: payload.active ?? true,
    // 평문 키는 보관하지 않고 마스킹된 표시용 값만 만든다.
    api_key_masked: payload.api_key ? `****${payload.api_key.slice(-4)}` : null,
    created_at: nowIso(),
    updated_at: nowIso(),
  };
  state.llmConnections.push(created);
  return created;
});

route('POST', /^\/api\/llm-connections\/test$/, (_p, { body }) => {
  const payload = body as LLMConnectionTestRequest;
  const saved = state.llmConnections.find((c) => c.id === payload.connection_id);
  const hasKey = Boolean(payload.api_key) || Boolean(saved?.api_key_masked);
  const model = payload.model ?? saved?.model ?? '';
  const response: ConnectionTestResponse = hasKey
    ? {
        ok: true,
        message: '연결에 성공했습니다. (최소 토큰 호출 — 실제 과금이 발생합니다)',
        latency_ms: 780,
        details: { model, input_tokens: 12, output_tokens: 3 },
      }
    : {
        ok: false,
        message: 'API 키가 없습니다.',
        latency_ms: null,
        details: {},
      };
  return response;
});

route('GET', /^\/api\/llm-connections\/(\d+)$/, ([id]) => {
  const found = state.llmConnections.find((c) => c.id === Number(id));
  if (!found) throw new ApiError(404, `LLM 연결 ${id} 을(를) 찾을 수 없습니다.`);
  return found;
});

route('PATCH', /^\/api\/llm-connections\/(\d+)$/, ([id], { body }) => {
  const found = state.llmConnections.find((c) => c.id === Number(id));
  if (!found) throw new ApiError(404, `LLM 연결 ${id} 을(를) 찾을 수 없습니다.`);
  const payload = body as LLMConnectionUpdate;
  if (payload.is_default) {
    state.llmConnections.forEach((c) => {
      c.is_default = false;
    });
  }
  if (payload.name != null) found.name = payload.name;
  if (payload.provider != null) found.provider = payload.provider;
  if (payload.model != null) found.model = payload.model;
  if (payload.base_url !== undefined) found.base_url = payload.base_url;
  if (payload.is_default != null) found.is_default = payload.is_default;
  if (payload.active != null) found.active = payload.active;
  if (payload.api_key) found.api_key_masked = `****${payload.api_key.slice(-4)}`;
  found.updated_at = nowIso();
  return found;
});

route('DELETE', /^\/api\/llm-connections\/(\d+)$/, ([id]) => {
  const found = state.llmConnections.find((c) => c.id === Number(id));
  if (found) {
    found.active = false;
    found.is_default = false;
  }
  return undefined;
});

// --- policies ---------------------------------------------------------------

route('GET', /^\/api\/policies$/, (_p, { query }) => {
  if (query.active === undefined || query.active === null) return state.policies;
  const want = String(query.active) === 'true';
  return state.policies.filter((policy) => policy.active === want);
});

route('POST', /^\/api\/policies$/, (_p, { body }) => {
  const payload = body as PolicyCreate;
  const created: PolicyRead = {
    id: nextId(),
    loki_connection_id: payload.loki_connection_id,
    name: payload.name,
    description: payload.description ?? null,
    logql: payload.logql,
    default_range_minutes: payload.default_range_minutes,
    max_lines: payload.max_lines,
    exclusions: payload.exclusions ?? [],
    max_samples_per_group: payload.max_samples_per_group,
    allow_ai_analysis: payload.allow_ai_analysis,
    daily_analysis_limit: payload.daily_analysis_limit ?? null,
    active: true,
    created_at: nowIso(),
    updated_at: nowIso(),
  };
  state.policies.push(created);
  return created;
});

route('POST', /^\/api\/policies\/preview$/, (_p, { body }) => {
  const payload = body as PolicyPreviewRequest;
  const logql = payload.logql.trim();

  if (!logql.startsWith('{')) {
    throw new ApiError(400, 'LogQL 은 스트림 셀렉터 `{...}` 로 시작해야 합니다.');
  }

  const matchesNothing = /nonexistent|__none__/.test(logql);
  const usesJson = logql.includes('| json');
  const handlesParseError = logql.includes('__error__');

  const source = groupSeeds.flatMap((seed) =>
    seed.samples.map((sample) => ({ line: sample.masked_log, service: seed.service })),
  );
  const serviceMatch = /service\s*=\s*"([^"]+)"/.exec(logql);
  const filtered = source
    .filter((row) => (serviceMatch ? row.service === serviceMatch[1] : true))
    .filter((row) => !payload.exclusions.some((pattern) => safeMatch(pattern, row.line)));

  const lines = matchesNothing ? [] : filtered.map((row) => row.line).slice(0, payload.limit);
  const fetched = matchesNothing ? 0 : Math.min(payload.limit, filtered.length * 37);

  const response: PolicyPreviewResponse = {
    fetched,
    dropped: usesJson && !handlesParseError ? Math.round(fetched * 0.05) : 0,
    truncated: fetched >= payload.limit,
    warnings: [
      ...(matchesNothing
        ? [
            {
              code: 'empty_result',
              message:
                '조회 결과가 비어 있습니다. 셀렉터 라벨이 실제로 존재하는지 확인하십시오.',
              count: 0,
            },
          ]
        : []),
      ...(usesJson && !handlesParseError
        ? [
            {
              code: 'parse_error',
              message:
                '`| json` 파싱에 실패한 라인이 후속 필터에서 조용히 사라집니다. ' +
                '`| __error__=""` 로 명시 처리하는 것을 검토하십시오.',
              count: Math.round(fetched * 0.05),
            },
          ]
        : []),
      ...(fetched >= payload.limit
        ? [
            {
              code: 'limit_reached',
              message: `요청 한도(${payload.limit})에 도달했습니다. 건수 집계에 이 값을 쓰지 마십시오.`,
              count: payload.limit,
            },
          ]
        : []),
    ],
    sample_lines: lines,
  };
  return response;
});

function safeMatch(pattern: string, text: string): boolean {
  try {
    return new RegExp(pattern, 'i').test(text);
  } catch {
    return text.toLowerCase().includes(pattern.toLowerCase());
  }
}

route('GET', /^\/api\/policies\/(\d+)$/, ([id]) => {
  const found = state.policies.find((p) => p.id === Number(id));
  if (!found) throw new ApiError(404, `정책 ${id} 을(를) 찾을 수 없습니다.`);
  return found;
});

route('PATCH', /^\/api\/policies\/(\d+)$/, ([id], { body }) => {
  const found = state.policies.find((p) => p.id === Number(id));
  if (!found) throw new ApiError(404, `정책 ${id} 을(를) 찾을 수 없습니다.`);
  const payload = body as PolicyUpdate;
  if (payload.loki_connection_id != null) found.loki_connection_id = payload.loki_connection_id;
  if (payload.name != null) found.name = payload.name;
  if (payload.description !== undefined) found.description = payload.description;
  if (payload.logql != null) found.logql = payload.logql;
  if (payload.default_range_minutes != null)
    found.default_range_minutes = payload.default_range_minutes;
  if (payload.max_lines != null) found.max_lines = payload.max_lines;
  if (payload.exclusions != null) found.exclusions = payload.exclusions;
  if (payload.max_samples_per_group != null)
    found.max_samples_per_group = payload.max_samples_per_group;
  if (payload.allow_ai_analysis != null) found.allow_ai_analysis = payload.allow_ai_analysis;
  if (payload.daily_analysis_limit !== undefined)
    found.daily_analysis_limit = payload.daily_analysis_limit;
  if (payload.active != null) found.active = payload.active;
  found.updated_at = nowIso();
  return found;
});

route('DELETE', /^\/api\/policies\/(\d+)$/, ([id]) => {
  const found = state.policies.find((p) => p.id === Number(id));
  // 실제 삭제가 아니라 비활성화다 — query_runs·분석 이력이 맥락을 잃지 않게.
  if (found) found.active = false;
  return undefined;
});

route('POST', /^\/api\/policies\/(\d+)\/query-runs$/, ([id], { body }) => {
  const policy = state.policies.find((p) => p.id === Number(id));
  if (!policy) throw new ApiError(404, `정책 ${id} 을(를) 찾을 수 없습니다.`);
  const payload = body as QueryRunCreateRequest;

  // 상한은 서버가 강제한다 — UI 가 보낸 limit 이 커도 정책 max_lines 로 잘린다.
  const limit = Math.min(payload.limit ?? policy.max_lines, policy.max_lines);
  const rangeEnd = payload.range_end ?? nowIso();
  const rangeStart =
    payload.range_start ??
    new Date(Date.parse(rangeEnd) - policy.default_range_minutes * 60_000).toISOString();

  const run: QueryRunRead = {
    id: nextId(),
    policy_id: policy.id,
    status: 'succeeded',
    started_at: nowIso(),
    finished_at: nowIso(),
    range_start: rangeStart,
    range_end: rangeEnd,
    fetched_count: Math.min(limit, 903),
    dropped_count: 47,
    warnings: [
      {
        code: 'parse_error',
        message: '`| json` 파싱에 실패한 라인이 있습니다.',
        count: 47,
      },
    ],
    error_message: null,
    group_count: groupSeeds.length,
  };
  state.queryRuns.push(run);
  return run;
});

route('GET', /^\/api\/query-runs\/(\d+)$/, ([id]) => {
  const found = state.queryRuns.find((run) => run.id === Number(id));
  if (!found) throw new ApiError(404, `조회 ${id} 을(를) 찾을 수 없습니다.`);
  return found;
});

route('GET', /^\/api\/query-runs\/(\d+)\/error-groups$/, ([id], { query }) => {
  const runId = Number(id);
  const limit = Number(query.limit ?? 50);
  const offset = Number(query.offset ?? 0);
  const items = groupSeeds
    .map((_seed, index) => groupSummary(index, runId))
    .sort((a, b) => b.count - a.count);
  const response: ErrorGroupListResponse = {
    query_run_id: runId,
    total: items.length,
    items: items.slice(offset, offset + limit),
  };
  return response;
});

// --- error groups / analysis ------------------------------------------------

route('GET', /^\/api\/error-groups\/(\d+)$/, ([id]) => groupDetail(Number(id)));

route('POST', /^\/api\/error-groups\/(\d+)\/analysis-jobs$/, ([id], { body }) => {
  const groupId = Number(id);
  const detail = groupDetail(groupId);
  const payload = (body ?? {}) as AnalysisJobCreateRequest;

  // 멱등: 같은 그룹에 진행 중인 작업이 있으면 새로 만들지 않고 기존 작업을 돌려준다.
  const active = allJobs().find(
    (job) =>
      job.error_group_id === groupId && (job.status === 'pending' || job.status === 'running'),
  );
  if (active) {
    return { ...active, reused: true } satisfies AnalysisJobCreateResponse;
  }

  const connection =
    state.llmConnections.find((c) => c.id === payload.llm_connection_id) ??
    state.llmConnections.find((c) => c.is_default && c.active);
  if (!connection) {
    throw new ApiError(400, '사용할 LLM 연결이 없습니다. 기본 연결을 지정하십시오.');
  }

  const policy = state.policies.find((p) => p.id === 1);
  if (policy && !policy.allow_ai_analysis) {
    throw new ApiError(403, '이 정책은 AI 분석을 허용하지 않습니다.');
  }

  const job: AnalysisJobRead = {
    id: nextId(),
    error_group_id: groupId,
    llm_connection_id: connection.id,
    fingerprint: detail.fingerprint,
    status: 'pending',
    provider: connection.provider,
    model: connection.model,
    prompt_version: 'v1',
    requested_at: nowIso(),
    started_at: null,
    completed_at: null,
    error_message: null,
    result: null,
    usage: null,
  };
  state.jobs.set(job.id, job);
  state.jobStartedAtMs.set(job.id, Date.now());
  return { ...job, reused: false } satisfies AnalysisJobCreateResponse;
});

/**
 * `GET /api/analysis-jobs` — 최신순 목록.
 *
 * 응답은 배열이 아니라 `{total, limit, offset, items}` 봉투이고 항목은 단건 조회와
 * 모양이 다르다(`result`·`usage` 없음, 심각도·요약은 평탄화). 라이브 백엔드와 mock 이
 * 다른 모양을 주면 화면이 mock 에서만 동작하므로 여기서도 같은 봉투로 맞춘다.
 */
route('GET', /^\/api\/analysis-jobs$/, () => {
  const items = allJobs()
    .sort((a, b) => Date.parse(b.requested_at) - Date.parse(a.requested_at))
    .map(toJobListItem);
  return {
    total: items.length,
    limit: items.length,
    offset: 0,
    items,
  } satisfies AnalysisJobListResponse;
});

route('GET', /^\/api\/analysis-jobs\/(\d+)$/, ([id]) => {
  const job = state.jobs.get(Number(id));
  if (!job) throw new ApiError(404, `분석 작업 ${id} 을(를) 찾을 수 없습니다.`);
  return advanceJob(job);
});

route('GET', /^\/api\/analysis-jobs\/(\d+)\/report$/, ([id]) => {
  const job = state.jobs.get(Number(id));
  if (!job) throw new ApiError(404, `분석 작업 ${id} 을(를) 찾을 수 없습니다.`);
  const advanced = advanceJob(job);
  if (advanced.status !== 'succeeded') {
    throw new ApiError(409, '분석이 완료된 뒤에 보고서를 뽑을 수 있습니다.');
  }
  return renderReportMarkdown(advanced, groupDetail(advanced.error_group_id));
});

// --- dashboard --------------------------------------------------------------

route('GET', /^\/api\/dashboard\/overview$/, (_p, { query }) => {
  const stepSeconds = Number(query.step_seconds ?? 300);
  const policyId = query.policy_id ? Number(query.policy_id) : null;
  const rangeStart = query.range_start ? String(query.range_start) : iso(-60);
  const rangeEnd = query.range_end ? String(query.range_end) : nowIso();
  const rangeMinutes = Math.max(
    5,
    Math.round((Date.parse(rangeEnd) - Date.parse(rangeStart)) / 60_000),
  );
  const top = Number(query.top ?? 10);

  const series = makeSeries(rangeMinutes, stepSeconds, seriesShapes.spike);
  const totalErrors = series.reduce((acc, point) => acc + point.value, 0);

  const groups = groupSeeds
    .map((_seed, index) => groupSummary(index, LATEST_RUN_ID))
    .sort((a, b) => b.count - a.count);

  const byServiceMap = new Map<string, number>();
  for (const group of groups) {
    const key = group.service ?? 'unknown';
    byServiceMap.set(key, (byServiceMap.get(key) ?? 0) + group.count);
  }
  const totalGroupCount = [...byServiceMap.values()].reduce((a, b) => a + b, 0) || 1;
  const by_service: ServiceErrorCount[] = [...byServiceMap.entries()]
    .map(([service, count]) => ({
      service,
      // metric 기준 총계를 그룹 비율로 나눠 준다 — 라인 수를 센 값이 아니다.
      count: Math.round((count / totalGroupCount) * totalErrors),
    }))
    .sort((a, b) => b.count - a.count);

  const response: DashboardOverviewResponse = {
    policy_id: policyId,
    query_run_id: LATEST_RUN_ID,
    range_start: rangeStart,
    range_end: rangeEnd,
    step_seconds: stepSeconds,
    total_errors: totalErrors,
    series,
    by_service,
    top_groups: groups.slice(0, top),
    warnings: [
      {
        code: 'parse_error',
        message:
          '`| json` 파싱 실패 라인이 후속 필터에서 제외되었습니다. 비정형 오류가 누락될 수 있습니다.',
        count: 47,
      },
    ],
  };
  return response;
});

// --- usage ------------------------------------------------------------------

route('GET', /^\/api\/usage$/, (_p, { query }) => {
  const rangeStart = query.range_start ? String(query.range_start) : iso(-60 * 24 * 7);
  const rangeEnd = query.range_end ? String(query.range_end) : nowIso();

  const jobs = allJobs()
    .filter((job) => job.usage)
    .filter((job) => !query.provider || job.provider === query.provider)
    .filter((job) => !query.model || job.model === query.model);

  const byKey = new Map<
    string,
    UsageAggregate & { _latencySum: number; _latencyN: number; _costN: number }
  >();
  for (const job of jobs) {
    const usage = job.usage!;
    const key = `${usage.provider}::${usage.model}`;
    const entry =
      byKey.get(key) ??
      {
        provider: usage.provider,
        model: usage.model,
        job_count: 0,
        failure_count: 0,
        input_tokens: 0,
        output_tokens: 0,
        estimated_cost: '0',
        avg_latency_ms: null,
        _latencySum: 0,
        _latencyN: 0,
        _costN: 0,
      };
    entry.job_count += 1;
    if (usage.status === 'failed') entry.failure_count += 1;
    entry.input_tokens += usage.input_tokens;
    entry.output_tokens += usage.output_tokens;
    if (usage.estimated_cost != null) {
      // 단가표에 없는 모델은 null 이다 — 0 으로 접으면 "쌌다"로 읽힌다.
      entry._costN += 1;
      entry.estimated_cost = (
        Number(entry.estimated_cost) + Number(usage.estimated_cost)
      ).toFixed(4);
    }
    if (usage.latency_ms != null) {
      entry._latencySum += usage.latency_ms;
      entry._latencyN += 1;
    }
    byKey.set(key, entry);
  }

  const items: UsageAggregate[] = [...byKey.values()].map((entry) => ({
    provider: entry.provider,
    model: entry.model,
    job_count: entry.job_count,
    failure_count: entry.failure_count,
    input_tokens: entry.input_tokens,
    output_tokens: entry.output_tokens,
    estimated_cost: entry._costN > 0 ? entry.estimated_cost : null,
    avg_latency_ms: entry._latencyN > 0 ? entry._latencySum / entry._latencyN : null,
  }));

  const costed = items.filter((item) => item.estimated_cost !== null);

  const response: UsageResponse = {
    range_start: rangeStart,
    range_end: rangeEnd,
    items,
    total_jobs: items.reduce((acc, item) => acc + item.job_count, 0),
    total_input_tokens: items.reduce((acc, item) => acc + item.input_tokens, 0),
    total_output_tokens: items.reduce((acc, item) => acc + item.output_tokens, 0),
    // 계산 가능한 항목이 하나도 없으면 합계도 null 이다 (0 으로 표시 금지).
    total_estimated_cost: costed.length
      ? costed.reduce((acc, item) => acc + Number(item.estimated_cost), 0).toFixed(4)
      : null,
  };
  return response;
});

// ------------------------------------------------------------------ 진입점

export async function mockRequest<T>(
  method: HttpMethod,
  path: string,
  options: RequestOptions,
): Promise<T> {
  const pathname = path.split('?')[0];
  for (const entry of routes) {
    if (entry.method !== method) continue;
    const match = entry.pattern.exec(pathname);
    if (!match) continue;
    const result = entry.handler(match.slice(1), {
      body: options.body,
      query: options.query ?? {},
    });
    return delay(structuredCloneSafe(result) as T);
  }
  throw new ApiError(404, `mock 라우트가 없습니다: ${method} ${pathname}`);
}

/** Map/함수가 섞여 들어와도 죽지 않게 감싼다. */
function structuredCloneSafe<T>(value: T): T {
  if (value === undefined || typeof value === 'string') return value;
  try {
    return structuredClone(value);
  } catch {
    return JSON.parse(JSON.stringify(value)) as T;
  }
}
