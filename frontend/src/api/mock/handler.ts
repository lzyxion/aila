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
 * - **인증**: auth 라우트와 `/health` 를 뺀 `/api/**` 는 세션이 필요하고(미인증 401),
 *   `viewer` 는 GET 만 할 수 있다(그 외 403). 판정은 라우팅 **앞**에서 한 번에 한다 —
 *   라우트마다 검사하면 새 라우트를 추가할 때 조용히 빠진다.
 */

import { ApiError, type HttpMethod, type RequestOptions } from '../client';
import type {
  AnalysisJobCreateRequest,
  AnalysisJobCreateResponse,
  AnalysisJobListItem,
  AnalysisJobListResponse,
  AnalysisJobRead,
  AnalysisJobSummary,
  AppSettingListResponse,
  AppSettingRead,
  AuthUser,
  ConnectionTestResponse,
  DashboardOverviewResponse,
  DashboardSummaryPolicy,
  DashboardSummaryResponse,
  ErrorGroupDetail,
  ErrorGroupListResponse,
  ErrorGroupSummary,
  LabelValuesResponse,
  LLMConnectionCreate,
  LLMConnectionRead,
  LLMConnectionTestRequest,
  LLMConnectionUpdate,
  LLMModelListRequest,
  LLMModelListResponse,
  LLMProviderName,
  LoginRequest,
  LokiConnectionCreate,
  LokiConnectionRead,
  LokiConnectionTestRequest,
  LokiConnectionUpdate,
  ModelPricingTable,
  PolicyCreate,
  PolicyPreviewRequest,
  PolicyPreviewResponse,
  PolicyRead,
  PolicyUpdate,
  QueryRunCreateRequest,
  QueryRunListResponse,
  QueryRunRead,
  ServiceErrorCount,
  SummarySeriesPoint,
  SummaryWarning,
  SettingValue,
  UsageAggregate,
  UsageBucket,
  UsageResponse,
  DashboardErrorGroupItem,
  DashboardErrorGroupsResponse,
  UserCreateRequest,
  UserListResponse,
  UserRead,
  UserUpdateRequest,
} from '../types';
import {
  asModelPricingTable,
  SETTING_DAILY_ANALYSIS_LIMIT,
  SETTING_MODEL_PRICING,
  SETTING_SAMPLE_RETENTION_DAYS,
  SETTING_TIMEZONE,
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
  userSeed,
} from './fixtures';
import { renderReportMarkdown } from '../../lib/report';

const LATENCY = Number(import.meta.env.VITE_MOCK_LATENCY_MS ?? 250);

// ------------------------------------------------------------------- state

interface MockState {
  /**
   * 지금 로그인한 사용자. 실제 세션은 httpOnly 쿠키이고 여기서는 메모리다 —
   * 화면 입장에서는 "로그인 요청 후 이어지는 요청이 통한다"는 모양만 같으면 된다.
   */
  session: AuthUser | null;
  /** 계정 목록 (계약 1). 비밀번호는 여기 없다 — `passwords` 가 따로 들고 있다. */
  users: UserRead[];
  /** username -> 비밀번호. 실제 백엔드는 scrypt 해시만 갖지만 mock 은 로그인만 재현하면 된다. */
  passwords: Record<string, string>;
  lokiConnections: LokiConnectionRead[];
  llmConnections: LLMConnectionRead[];
  policies: PolicyRead[];
  queryRuns: QueryRunRead[];
  /** job.id -> job */
  jobs: Map<number, AnalysisJobRead>;
  /** job.id -> 시뮬레이션 시작 시각(ms). 진행 상태를 시간으로 계산한다. */
  jobStartedAtMs: Map<number, number>;
  /** 예약 설정 3 종. 행이 없는 키는 값이 null 이고 기본값이 적용된다. */
  settings: Record<string, { value: SettingValue; updated_at: string | null }>;
  nextId: number;
}

/**
 * 단가표 초깃값. **claude 만 있고 gpt 는 없다** — 단가 미등록 모델의 추정 비용이
 * `-` 로 남는 상태(계약)와 사용량 화면의 "단가 등록" 인라인 UI 를 둘 다 확인하기 위해서다.
 */
const MODEL_PRICING_SEED: ModelPricingTable = {
  'claude-sonnet-4-6': { input_per_1k: 0.003, output_per_1k: 0.015, currency: 'USD' },
};

/** 설정 키별 서버 기본값 (`app.app_settings.service.default_value`). */
const SETTING_DEFAULTS: Record<string, SettingValue> = {
  [SETTING_DAILY_ANALYSIS_LIMIT]: 50,
  [SETTING_MODEL_PRICING]: {},
  [SETTING_SAMPLE_RETENTION_DAYS]: 14,
  [SETTING_TIMEZONE]: 'Asia/Seoul',
};

const SETTING_DESCRIPTIONS: Record<string, string> = {
  [SETTING_DAILY_ANALYSIS_LIMIT]:
    '전역 일일 분석 횟수 상한 (로컬 자정 기준). 0 이면 분석을 시작할 수 없다.',
  [SETTING_MODEL_PRICING]:
    '모델 단가표 {model: {input_per_1k, output_per_1k, currency}}. ' +
    '표에 없는 모델의 추정 비용은 0 이 아니라 None 으로 남는다.',
  [SETTING_SAMPLE_RETENTION_DAYS]:
    'error_samples 보존 일수. 지난 샘플은 삭제한다. 0 이면 자동 삭제를 끈다.',
  [SETTING_TIMEZONE]: '일일 분석 한도의 리셋 기준 시간대 (IANA 이름).',
};

/**
 * mock 계정의 초기 비밀번호. **viewer 가 하나 있어야** 읽기 전용 화면을 실제로 눌러 볼 수
 * 있다 — 쓰기 UI 를 감추는 코드가 맞는지는 admin 화면만 봐서는 검증되지 않는다.
 *
 * 역할·활성 여부는 `userSeed`(계정 목록)가 단일 출처다. 여기 없는 계정은 로그인할 수
 * 없고(비밀번호 미설정), 그 상태는 계정 관리 화면에서 "비밀번호 재설정"으로 풀린다.
 */
const INITIAL_PASSWORDS: Record<string, string> = {
  admin: 'admin',
  viewer: 'viewer',
};

/**
 * mock 세션을 `sessionStorage` 에 둔다.
 *
 * 실제 세션은 쿠키라 **새로고침해도 살아 있다.** 메모리에만 두면 F5 한 번에 로그인
 * 화면으로 튕겨서, mock 으로는 "세션이 유지되는 화면"을 확인할 수 없다. 브라우저가
 * 아닌 곳(smoke 는 node 에서 돈다)에서는 조용히 메모리만 쓴다.
 */
const SESSION_KEY = 'aila.mock.session';

function loadSession(): AuthUser | null {
  try {
    const raw = globalThis.sessionStorage?.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

function persistSession(user: AuthUser | null): void {
  try {
    if (user) globalThis.sessionStorage?.setItem(SESSION_KEY, JSON.stringify(user));
    else globalThis.sessionStorage?.removeItem(SESSION_KEY);
  } catch {
    /* 브라우저가 아니거나 저장이 막혀 있다 — 메모리 세션으로 충분하다. */
  }
}

const state: MockState = {
  session: loadSession(),
  users: structuredClone(userSeed),
  passwords: { ...INITIAL_PASSWORDS },
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
      triggered_by: 'schedule',
    },
    // 이력 화면이 상태·경고·실패를 모두 보여줘야 하므로 과거 실행을 몇 개 더 둔다.
    {
      id: 4998,
      policy_id: 1,
      status: 'succeeded',
      started_at: iso(-370),
      finished_at: iso(-369),
      range_start: iso(-430),
      range_end: iso(-370),
      fetched_count: 500,
      dropped_count: 12,
      warnings: [
        {
          code: 'range_clamped',
          message: '요청 기간이 정책 기본 기간(60분)으로 조정되었습니다.',
          count: null,
        },
        {
          code: 'limit_reached',
          message: '요청 한도(500)에 도달했습니다. 건수 집계에 이 값을 쓰지 마십시오.',
          count: 500,
        },
      ],
      error_message: null,
      group_count: 4,
      triggered_by: 'manual',
    },
    {
      id: 4997,
      policy_id: 1,
      status: 'failed',
      started_at: iso(-1500),
      finished_at: iso(-1500),
      range_start: iso(-1560),
      range_end: iso(-1500),
      fetched_count: 0,
      dropped_count: 0,
      warnings: [],
      error_message: 'Loki 응답이 500 입니다: parse error at line 1: syntax error',
      group_count: 0,
      triggered_by: 'schedule',
    },
    {
      id: 4996,
      policy_id: 2,
      status: 'succeeded',
      started_at: iso(-240),
      finished_at: iso(-239),
      range_start: iso(-420),
      range_end: iso(-240),
      fetched_count: 318,
      dropped_count: 5,
      warnings: [],
      error_message: null,
      group_count: 3,
      triggered_by: 'manual',
    },
  ],
  jobs: new Map(analysisJobSeed.map((job) => [job.id, structuredClone(job)])),
  jobStartedAtMs: new Map(),
  settings: {
    [SETTING_MODEL_PRICING]: {
      value: structuredClone(MODEL_PRICING_SEED) as SettingValue,
      updated_at: iso(-60 * 24 * 4),
    },
  },
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
  // 단가는 **완료 시점 표**로 계산하고 쓴 단가를 스냅샷으로 복사한다. 표에 없으면
  // 값을 지어내지 않고 null 로 남긴다 (0 은 "쌌다"로 읽힌다). 나중에 단가를 등록해도
  // 이미 기록된 이 값은 바뀌지 않는다 — 소급 계산은 없다.
  const entry = modelPricingTable()[job.model];
  const inputRate = numericRate(entry?.input_per_1k);
  const outputRate = numericRate(entry?.output_per_1k);
  const priced = inputRate !== null || outputRate !== null;
  const cost = ((inputTokens / 1000) * (inputRate ?? 0) + (outputTokens / 1000) * (outputRate ?? 0));
  job.usage = {
    provider: job.provider,
    model: job.model,
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    estimated_cost: priced ? cost.toFixed(4) : null,
    pricing_snapshot: priced
      ? {
          model: job.model,
          input_per_1k: entry?.input_per_1k ?? null,
          output_per_1k: entry?.output_per_1k ?? null,
          currency: entry?.currency ?? 'USD',
        }
      : null,
    latency_ms: Math.round(elapsed),
    status: 'succeeded',
    failure_reason: null,
  };
  return job;
}

function modelPricingTable(): ModelPricingTable {
  return asModelPricingTable(state.settings[SETTING_MODEL_PRICING]?.value);
}

function numericRate(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
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
    triggered_by: job.triggered_by,
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

// --- auth -------------------------------------------------------------------

/**
 * 실패는 **401** 이고 사용자명·비밀번호 중 무엇이 틀렸는지 구분해 알리지 않는다 —
 * 구분해 주면 존재하는 계정 이름을 확인하는 데 쓰인다.
 */
route('POST', /^\/api\/auth\/login$/, (_p, { body }) => {
  const payload = (body ?? {}) as Partial<LoginRequest>;
  const username = (payload.username ?? '').trim();
  const found = state.users.find((user) => user.username === username && user.active);
  // 비활성 계정·비밀번호 불일치·없는 계정을 **구분하지 않는다** — 구분하면 계정 이름
  // 열거가 된다 (실제 백엔드와 같은 규칙).
  if (!found || state.passwords[username] !== payload.password) {
    throw new ApiError(401, '사용자명 또는 비밀번호가 올바르지 않습니다.');
  }
  state.session = { username: found.username, role: found.role };
  persistSession(state.session);
  return state.session satisfies AuthUser;
});

route('POST', /^\/api\/auth\/logout$/, () => {
  state.session = null;
  persistSession(null);
  return undefined;
});

route('GET', /^\/api\/auth\/me$/, () => {
  if (!state.session) throw new ApiError(401, '로그인이 필요합니다.');
  return state.session satisfies AuthUser;
});

// --- 계정 관리 (계약 1 — admin 전용) ----------------------------------------

/**
 * "마지막 남은 active admin" 인가.
 *
 * **강등·비활성을 막는 유일한 장치**다. 이게 없으면 admin 을 전부 viewer 로 바꾼 순간
 * 아무도 계정을 되돌릴 수 없는 상태가 되고, 그건 화면에서는 성공으로 보인다.
 */
function isLastActiveAdmin(user: UserRead): boolean {
  if (user.role !== 'admin' || !user.active) return false;
  return state.users.filter((row) => row.role === 'admin' && row.active).length <= 1;
}

/**
 * 세션 무효화. 실제 백엔드는 `user_sessions` 행을 지운다 — mock 에는 세션이 하나뿐이라
 * "그 계정이 지금 로그인한 계정이면 끊는다"로 같은 결과를 만든다.
 */
function invalidateSessionsFor(username: string): void {
  if (state.session?.username === username) {
    state.session = null;
    persistSession(null);
  }
}

function requireUser(id: string): UserRead {
  const found = state.users.find((user) => user.id === Number(id));
  if (!found) throw new ApiError(404, `계정 ${id} 을(를) 찾을 수 없습니다.`);
  return found;
}

route('GET', /^\/api\/auth\/users$/, () => {
  const items = [...state.users].sort((a, b) => a.id - b.id);
  return { total: items.length, items } satisfies UserListResponse;
});

route('POST', /^\/api\/auth\/users$/, (_p, { body }) => {
  const payload = (body ?? {}) as Partial<UserCreateRequest>;
  const username = (payload.username ?? '').trim();
  if (!username) throw new ApiError(422, '사용자명을 입력하십시오.');
  if (!payload.password) throw new ApiError(422, '비밀번호를 입력하십시오.');
  if (state.users.some((user) => user.username === username)) {
    throw new ApiError(409, `'${username}' 계정이 이미 있습니다.`);
  }
  const created: UserRead = {
    id: nextId(),
    username,
    role: payload.role === 'admin' ? 'admin' : 'viewer',
    active: true,
    created_at: nowIso(),
  };
  state.users.push(created);
  state.passwords[username] = payload.password;
  return created;
});

route('PATCH', /^\/api\/auth\/users\/(\d+)$/, ([id], { body }) => {
  const user = requireUser(id);
  const payload = (body ?? {}) as UserUpdateRequest;

  // 마지막 admin 보호는 **병합 후 실효값**으로 본다 — 요청 하나만 보면 "역할만 바꾼"
  // 요청과 "활성만 끈" 요청이 각각은 안전해 보인다.
  if (payload.role === 'viewer' && isLastActiveAdmin(user)) {
    throw new ApiError(
      409,
      '마지막 남은 활성 admin 계정은 viewer 로 바꿀 수 없습니다. 다른 admin 을 먼저 만드십시오.',
    );
  }
  if (payload.active === false && isLastActiveAdmin(user)) {
    throw new ApiError(
      409,
      '마지막 남은 활성 admin 계정은 비활성화할 수 없습니다. 다른 admin 을 먼저 만드십시오.',
    );
  }

  if (payload.role) user.role = payload.role;
  if (payload.active != null) user.active = payload.active;
  if (payload.password) state.passwords[user.username] = payload.password;

  // 비활성화·비밀번호 변경은 그 계정의 세션을 전부 무효화한다 (계약).
  if (payload.active === false || payload.password) invalidateSessionsFor(user.username);
  return user;
});

route('DELETE', /^\/api\/auth\/users\/(\d+)$/, ([id]) => {
  const user = requireUser(id);
  // 자기 자신을 비활성화하면 지금 이 화면이 곧바로 잠긴다 — 서버가 막는다.
  if (state.session?.username === user.username) {
    throw new ApiError(409, '자기 자신을 비활성화할 수 없습니다.');
  }
  if (isLastActiveAdmin(user)) {
    throw new ApiError(409, '마지막 남은 활성 admin 계정은 비활성화할 수 없습니다.');
  }
  // 실제 삭제가 아니라 active=false + 세션 무효화다.
  user.active = false;
  invalidateSessionsFor(user.username);
  return undefined;
});

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

/**
 * `POST /api/llm-connections/models` — 프로바이더 모델 목록 (계약 1).
 *
 * **조회지만 POST 다** — api_key 를 쿼리스트링에 실으면 평문 키가 액세스 로그에 남는다.
 * 라이브 백엔드와 같은 모양 `{provider, models}` 로 준다. 실패 경로도 흉내 낸다 —
 * 키/base URL 이 없으면 400 이고, 화면은 오류로 막는 대신 **자유 입력으로 폴백**한다.
 */
route('POST', /^\/api\/llm-connections\/models$/, (_p, { body }) => {
  const payload = (body ?? {}) as LLMModelListRequest;
  const saved =
    payload.connection_id != null
      ? state.llmConnections.find((c) => c.id === payload.connection_id)
      : undefined;
  const provider = (payload.provider ?? saved?.provider ?? '') as LLMProviderName;
  const apiKey = payload.api_key || (saved?.api_key_masked ?? null);
  const baseUrl = payload.base_url || (saved?.base_url ?? null);

  if (!provider) throw new ApiError(400, 'provider 를 지정하십시오.');

  if (provider === 'openai_compatible') {
    if (!baseUrl) {
      throw new ApiError(400, 'OpenAI 호환 엔드포인트는 base URL 이 필요합니다.');
    }
    return {
      provider,
      models: ['llm-mock-1', 'qwen3-32b-instruct', 'llama-3.3-70b-instruct'],
    } satisfies LLMModelListResponse;
  }

  if (!apiKey) {
    throw new ApiError(400, 'API 키가 없어 모델 목록을 조회할 수 없습니다.');
  }

  const models =
    provider === 'anthropic'
      ? ['claude-opus-4-6', 'claude-sonnet-4-6', 'claude-haiku-4-6']
      : ['gpt-5.2', 'gpt-5.2-mini', 'o5-mini'];
  return { provider, models } satisfies LLMModelListResponse;
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
    schedule_enabled: payload.schedule_enabled ?? false,
    // 스케줄이 꺼져 있으면 주기를 남기지 않는다 (백엔드 기본값과 같은 판정).
    schedule_interval_minutes: payload.schedule_enabled
      ? (payload.schedule_interval_minutes ?? null)
      : null,
    auto_analyze_new: (payload.schedule_enabled && payload.auto_analyze_new) ?? false,
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
  if (payload.schedule_enabled != null) found.schedule_enabled = payload.schedule_enabled;
  if (payload.schedule_interval_minutes !== undefined)
    found.schedule_interval_minutes = payload.schedule_interval_minutes;
  if (payload.auto_analyze_new != null) found.auto_analyze_new = payload.auto_analyze_new;
  // 스케줄을 끄면 주기·자동 분석도 함께 내려간다 — 꺼진 정책에 설정만 남아 있으면
  // 다음에 켰을 때 예전 값으로 조용히 돌기 시작한다.
  if (found.schedule_enabled === false) {
    found.schedule_interval_minutes = null;
    found.auto_analyze_new = false;
  }
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
    // 화면에서 누른 실행은 언제나 manual 이다. schedule 은 스케줄러만 만든다.
    triggered_by: 'manual',
  };
  state.queryRuns.push(run);
  return run;
});

/**
 * `GET /api/policies/{id}/query-runs` — 정책 실행 이력 (계약 2, 최신순 페이지네이션).
 * 라이브 백엔드와 같은 봉투 `{total, limit, offset, items}` 로 준다.
 */
route('GET', /^\/api\/policies\/(\d+)\/query-runs$/, ([id], { query }) => {
  const policyId = Number(id);
  if (!state.policies.some((policy) => policy.id === policyId)) {
    throw new ApiError(404, `정책 ${id} 을(를) 찾을 수 없습니다.`);
  }
  const limit = Number(query.limit ?? 20);
  const offset = Number(query.offset ?? 0);
  const items = state.queryRuns
    .filter((run) => run.policy_id === policyId)
    .sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at));
  return {
    total: items.length,
    limit,
    offset,
    items: items.slice(offset, offset + limit),
  } satisfies QueryRunListResponse;
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
    // 화면에서 누른 분석은 언제나 manual 이다 (계약: 분석의 자동 실행은 스케줄 경로뿐).
    triggered_by: 'manual',
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
route('GET', /^\/api\/analysis-jobs$/, (_p, { query }) => {
  const limit = Number(query.limit ?? 50);
  const offset = Number(query.offset ?? 0);
  const status = query.status ? String(query.status) : null;
  const needle = query.q ? String(query.q).trim().toLowerCase() : '';
  const from = query.requested_from ? Date.parse(String(query.requested_from)) : null;
  const to = query.requested_to ? Date.parse(String(query.requested_to)) : null;

  const all = allJobs()
    .sort((a, b) => Date.parse(b.requested_at) - Date.parse(a.requested_at))
    .map(toJobListItem)
    .filter((job) => (status ? job.status === status : true))
    .filter((job) => {
      if (from === null && to === null) return true;
      const at = Date.parse(job.requested_at);
      if (from !== null && at < from) return false;
      if (to !== null && at > to) return false;
      return true;
    })
    .filter((job) => {
      if (!needle) return true;
      // 계약: 서비스·모델·normalized_message·fingerprint 부분 일치.
      return [job.service, job.model, job.normalized_message, job.fingerprint]
        .filter((value): value is string => typeof value === 'string')
        .some((value) => value.toLowerCase().includes(needle));
    });

  return {
    // total 은 **필터를 적용한 뒤의 건수**다 — 전체 건수를 주면 페이지네이션이 어긋난다.
    total: all.length,
    limit,
    offset,
    items: all.slice(offset, offset + limit),
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

/**
 * `GET /api/dashboard/summary` — 홈의 정책 카드 그리드 (계약 3).
 *
 * 계약대로 만들어야 하는 지점 몇 가지:
 * - `unanalyzed_group_count` 는 **최근 성공한 run** 의 그룹 중 fingerprint 분석 이력이
 *   전혀 없는 수다. 실패한 run 은 기준이 되지 않는다 (그룹이 없으니 0 이 아니라 무의미).
 * - `total_errors_24h` 는 metric 실패를 **null** 로 표현한다. 화면이 `-` 와 0 을 다르게
 *   그리는지 확인해야 하므로 정책 하나는 일부러 null 로 둔다.
 * - `last_run` 이 없는 정책(한 번도 실행 안 함)도 카드가 되어야 한다.
 */
route('GET', /^\/api\/dashboard\/summary$/, () => {
  const jobs = allJobs();
  const analyzedFingerprints = new Set(jobs.map((job) => job.fingerprint));

  const policies: DashboardSummaryPolicy[] = state.policies.map((policy) => {
    const runs = state.queryRuns
      .filter((run) => run.policy_id === policy.id)
      .sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at));
    const lastRun = runs[0] ?? null;
    const lastSucceeded = runs.find((run) => run.status === 'succeeded') ?? null;

    // 최근 성공 run 의 그룹 중 fingerprint 이력이 전혀 없는 것만 센다.
    const unanalyzed = lastSucceeded
      ? groupSeeds
          .slice(0, Math.min(lastSucceeded.group_count, groupSeeds.length))
          .filter((seed) => !analyzedFingerprints.has(seed.fingerprint)).length
      : 0;

    const warnings: SummaryWarning[] = [];
    if (!policy.active) {
      warnings.push({
        code: 'policy_inactive',
        message: '비활성 정책이라 스케줄·실행이 돌지 않습니다.',
      });
    }
    if (policy.schedule_enabled && policy.schedule_interval_minutes == null) {
      warnings.push({
        code: 'schedule_without_interval',
        message: '스케줄이 켜져 있지만 실행 주기가 설정되지 않았습니다.',
      });
    }
    if (lastRun?.status === 'failed') {
      warnings.push({
        code: 'last_run_failed',
        message: lastRun.error_message ?? '최근 실행이 실패했습니다.',
      });
    }

    // 정책 3 은 metric 쿼리가 실패한 상태로 둔다 — null 과 0 이 화면에서 갈리는지 본다.
    const metricFailed = policy.id === 3;
    if (metricFailed) {
      warnings.push({
        code: 'count_query_failed',
        message: '최근 24시간 metric 쿼리에 실패했습니다. 0 건이라는 뜻이 아닙니다.',
      });
    }

    /*
      스파크라인용 24h 포인트 (step 3600).

      계약의 핵심은 "**같은** count_over_time 결과를 재사용한다"는 것이다 — 카드마다 Loki
      를 한 번 더 두드리면 정책 20 개짜리 첫 화면이 조회 20 번 느려진다. 그래서 mock 도
      시리즈를 **먼저** 만들고 합계를 그 시리즈에서 뽑는다 (반대로 하면 두 값이 어긋난다).
      metric 이 실패한 정책은 시리즈가 빈 배열이고 합계는 null 이다 — 0 이 아니다.
    */
    const series24h: SummarySeriesPoint[] = metricFailed
      ? []
      : makeSeries(24 * 60, 3600, (t) => (policy.id === 1 ? seriesShapes.spike(t) : seriesShapes.steady(t)) * (10 + policy.id)).map(
          (point) => ({ timestamp: point.timestamp, value: point.value }),
        );
    const total24h = metricFailed
      ? null
      : series24h.reduce((acc, point) => acc + point.value, 0);

    return {
      policy_id: policy.id,
      name: policy.name,
      active: policy.active,
      schedule_enabled: policy.schedule_enabled ?? false,
      schedule_interval_minutes: policy.schedule_interval_minutes ?? null,
      last_run: lastRun
        ? {
            id: lastRun.id,
            started_at: lastRun.started_at,
            status: lastRun.status,
            fetched_count: lastRun.fetched_count,
            group_count: lastRun.group_count,
            warnings: lastRun.warnings,
          }
        : null,
      unanalyzed_group_count: unanalyzed,
      total_errors_24h: total24h,
      series_24h: series24h,
      warnings,
    };
  });

  return { generated_at: nowIso(), policies } satisfies DashboardSummaryResponse;
});

/**
 * `GET /api/dashboard/error-groups` — 전 정책의 오류 그룹 한 목록 (계약 4).
 *
 * 대상은 **활성 정책의 최신 성공 query-run** 그룹이다. 정렬은 count desc, last_seen desc —
 * "지금 가장 많이 터지는 오류"가 위로 온다. 항목에 policy_id·policy_name 이 붙는 이유는
 * 회차 하나짜리 목록과 달리 어느 정책이 잡은 그룹인지가 화면에서 사라지면 안 되기 때문이다.
 */
route('GET', /^\/api\/dashboard\/error-groups$/, (_p, { query }) => {
  const limit = Number(query.limit ?? 20);
  const offset = Number(query.offset ?? 0);

  /*
    활성 정책 × 최신 성공 run 만 대상이다. 실제 백엔드에서는 그룹 id 가 회차마다 새로
    생겨 정책끼리 겹치지 않는다 — mock 은 seed 를 공유하므로 정책별로 **겹치지 않게 나눠**
    같은 성질을 만든다 (같은 id 가 두 줄로 나오면 화면 키가 충돌한다).
  */
  const withRuns = state.policies
    .filter((policy) => policy.active)
    .map((policy) => ({
      policy,
      run: state.queryRuns
        .filter((run) => run.policy_id === policy.id && run.status === 'succeeded')
        .sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at))[0],
    }))
    .filter((entry) => entry.run !== undefined);

  const items: DashboardErrorGroupItem[] = [];
  if (withRuns.length > 0) {
    groupSeeds.forEach((_seed, index) => {
      const owner = withRuns[index % withRuns.length];
      items.push({
        ...groupSummary(index, owner.run.id),
        policy_id: owner.policy.id,
        policy_name: owner.policy.name,
      });
    });
  }

  items.sort(
    (a, b) => b.count - a.count || Date.parse(b.last_seen) - Date.parse(a.last_seen),
  );

  return {
    total: items.length,
    limit,
    offset,
    items: items.slice(offset, offset + limit),
  } satisfies DashboardErrorGroupsResponse;
});

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

/**
 * 분석 작업 -> 정책 매핑 (mock 전용).
 *
 * 실제 백엔드는 error_group -> query_run -> policy 로 조인한다. mock 에는 그 사슬이 없어
 * 표로 대신하고, **일부러 한 그룹을 `null` 로 둔다** — 정책 연결이 끊긴 작업이 `unknown`
 * 버킷으로 들어가는 계약을 화면에서 확인할 수 있어야 한다 (실제로는 정책이 지워진 뒤
 * 남은 오래된 분석 이력이 여기 해당한다).
 */
const GROUP_POLICY: Record<number, number | null> = {
  101: 1,
  102: 2,
  103: 2,
  104: 1,
  105: null,
  106: 1,
};

/** `app_settings.timezone` 기준 로컬 날짜 `YYYY-MM-DD`. 서버 로케일이 아니라 설정값이다. */
function localDateKey(iso8601: string): string {
  const zone = String(state.settings[SETTING_TIMEZONE]?.value ?? SETTING_DEFAULTS[SETTING_TIMEZONE]);
  try {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: zone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).format(new Date(iso8601));
  } catch {
    // 잘못된 타임존이 저장돼도 사용량 화면이 죽지는 않는다 (한도 판정과 같은 규칙).
    return iso8601.slice(0, 10);
  }
}

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
    // 분해를 요청하지 않으면 **null** 이지 빈 배열이 아니다 — "요청하지 않았다"와
    // "분해했더니 아무것도 없었다"는 화면에서 다른 문구가 되어야 한다. 라이브 백엔드도
    // 키를 빼지 않고 null 을 싣는다(봉투를 같게 유지하는 것이 mock 의 존재 이유다).
    buckets: null,
  };

  const groupBy = query.group_by ? String(query.group_by) : null;
  if (groupBy === 'day' || groupBy === 'policy') {
    response.buckets = buildUsageBuckets(jobs, groupBy);
  }
  return response;
});

/**
 * `group_by` 분해.
 *
 * 비용은 **계산 가능한 항목이 하나도 없으면 null** 이다 — 0 으로 접으면 "이 날은 쌌다"로
 * 읽힌다. 화면도 그 칸의 막대를 그리지 않고 `-` 로 쓴다.
 */
function buildUsageBuckets(
  jobs: AnalysisJobRead[],
  groupBy: 'day' | 'policy',
): UsageBucket[] {
  const byKey = new Map<string, UsageBucket & { _costN: number }>();

  for (const job of jobs) {
    const usage = job.usage!;
    let key: string;
    let label: string;
    if (groupBy === 'day') {
      key = localDateKey(job.requested_at);
      label = key;
    } else {
      const policyId = GROUP_POLICY[job.error_group_id] ?? null;
      const policy = policyId === null ? undefined : state.policies.find((p) => p.id === policyId);
      // 정책 연결이 끊긴 작업은 key="unknown" 이다 — 버리면 합계가 어긋난다.
      key = policy ? String(policy.id) : 'unknown';
      label = policy ? policy.name : '정책 연결 없음';
    }

    const entry =
      byKey.get(key) ??
      {
        key,
        label,
        input_tokens: 0,
        output_tokens: 0,
        estimated_cost: '0',
        job_count: 0,
        failure_count: 0,
        _costN: 0,
      };
    entry.job_count += 1;
    if (usage.status === 'failed') entry.failure_count += 1;
    entry.input_tokens += usage.input_tokens;
    entry.output_tokens += usage.output_tokens;
    if (usage.estimated_cost != null) {
      entry._costN += 1;
      entry.estimated_cost = (
        Number(entry.estimated_cost) + Number(usage.estimated_cost)
      ).toFixed(4);
    }
    byKey.set(key, entry);
  }

  const rows = [...byKey.values()].map(({ _costN, ...bucket }) => ({
    ...bucket,
    estimated_cost: _costN > 0 ? bucket.estimated_cost : null,
  }));

  // 날짜는 시간순, 정책은 토큰 많은 순. unknown 은 이름 정렬에 끼지 않게 맨 뒤로 민다.
  return groupBy === 'day'
    ? rows.sort((a, b) => a.key.localeCompare(b.key))
    : rows.sort(
        (a, b) =>
          Number(a.key === 'unknown') - Number(b.key === 'unknown') ||
          b.input_tokens + b.output_tokens - (a.input_tokens + a.output_tokens),
      );
}

// --- settings ---------------------------------------------------------------

/**
 * 예약 설정 3 종. 라이브와 같은 규칙을 흉내 낸다 —
 * 화이트리스트 밖 키는 404, 단가표 형식이 깨지면 422, `PUT` 은 키를 **통째로 교체**한다.
 * (그래서 화면이 병합해서 보내지 않으면 다른 모델 단가가 사라지는 것도 그대로 재현된다.)
 */
function settingRead(key: string): AppSettingRead {
  const row = state.settings[key];
  const value = row?.value ?? null;
  return {
    key,
    value,
    description: SETTING_DESCRIPTIONS[key],
    updated_at: row?.updated_at ?? null,
    effective_value: value !== null ? value : SETTING_DEFAULTS[key],
  };
}

function requireSettingKey(key: string): void {
  if (!(key in SETTING_DEFAULTS)) {
    const allowed = Object.keys(SETTING_DEFAULTS).sort().join(', ');
    throw new ApiError(404, `'${key}' 는 설정 키가 아닙니다. 사용할 수 있는 키: ${allowed}.`);
  }
}

/** 단가표 형식 검증 — 깨진 표는 저장 자체를 막는다 (백엔드와 같은 판정). */
function validateSettingValue(key: string, value: SettingValue): SettingValue {
  if (value === null || value === undefined) return null;
  if (key === SETTING_DAILY_ANALYSIS_LIMIT || key === SETTING_SAMPLE_RETENTION_DAYS) {
    if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
      throw new ApiError(422, `${key} 는 0 이상의 정수여야 합니다.`);
    }
    return value;
  }
  if (key === SETTING_TIMEZONE) {
    if (typeof value !== 'string' || !value.trim()) {
      throw new ApiError(422, 'timezone 은 IANA 시간대 이름이어야 합니다.');
    }
    return value;
  }
  if (typeof value !== 'object' || Array.isArray(value)) {
    throw new ApiError(422, 'model_pricing 은 객체여야 합니다.');
  }
  for (const [model, entry] of Object.entries(value as Record<string, unknown>)) {
    if (!model.trim()) {
      throw new ApiError(422, 'model_pricing 의 키는 비어 있지 않은 모델명이어야 합니다.');
    }
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) {
      throw new ApiError(422, `model_pricing['${model}'] 은 객체여야 합니다.`);
    }
    const row = entry as Record<string, unknown>;
    if (row.input_per_1k === undefined && row.output_per_1k === undefined) {
      throw new ApiError(
        422,
        `model_pricing['${model}'] 에 input_per_1k 또는 output_per_1k 가 있어야 합니다.`,
      );
    }
    for (const field of ['input_per_1k', 'output_per_1k'] as const) {
      const rate = row[field];
      if (rate === undefined || rate === null) continue;
      if (typeof rate !== 'number' || Number.isNaN(rate)) {
        throw new ApiError(422, `model_pricing['${model}'].${field} 는 숫자여야 합니다.`);
      }
      if (rate < 0) {
        throw new ApiError(422, `model_pricing['${model}'].${field} 는 0 이상이어야 합니다.`);
      }
    }
  }
  return value;
}

route('GET', /^\/api\/settings$/, () => {
  return {
    items: Object.keys(SETTING_DEFAULTS).sort().map(settingRead),
  } satisfies AppSettingListResponse;
});

route('GET', /^\/api\/settings\/([A-Za-z0-9_]+)$/, ([key]) => {
  requireSettingKey(key);
  return settingRead(key);
});

route('PUT', /^\/api\/settings\/([A-Za-z0-9_]+)$/, ([key], { body }) => {
  requireSettingKey(key);
  const payload = (body ?? {}) as { value?: SettingValue };
  const validated = validateSettingValue(key, payload.value ?? null);
  state.settings[key] = { value: validated, updated_at: nowIso() };
  return settingRead(key);
});

// ------------------------------------------------------------------ 진입점

/**
 * 인증·권한 판정. **라우팅보다 먼저** 한 번에 한다 — 라우트마다 검사하면 새 라우트를
 * 추가할 때 조용히 빠지고, 그러면 mock 에서만 통과하는 경로가 생긴다.
 *
 * - 로그인·로그아웃·`/health` 는 통과 (`PUBLIC_API_PATHS` 와 같은 화이트리스트)
 * - 세션 없음 → 401 (화면은 client 의 인터셉트로 /login 으로 간다)
 * - `viewer` 의 비-GET → 403 (읽기 전용 계정의 진짜 방어선)
 * - **계정 관리(`/api/auth/users`)는 GET 도 admin 전용** → viewer 는 403
 *
 * `/api/auth/users` 를 auth 예외에 넣지 않는 것이 핵심이다 — `/api/auth/` 접두사로
 * 통째로 열어 두면 계정 목록이 미인증에도 열린다.
 */
function authorize(method: HttpMethod, pathname: string): void {
  if (pathname === '/api/auth/login' || pathname === '/api/auth/logout') return;
  if (pathname === '/health') return;
  if (!state.session) {
    throw new ApiError(401, '로그인이 필요합니다.');
  }
  if (pathname.startsWith('/api/auth/users') && state.session.role !== 'admin') {
    throw new ApiError(403, '계정 관리는 admin 계정만 할 수 있습니다.');
  }
  if (pathname === '/api/auth/me') return;
  if (state.session.role === 'viewer' && method !== 'GET') {
    throw new ApiError(
      403,
      '읽기 전용(viewer) 계정은 조회만 할 수 있습니다. 실행·저장·삭제는 admin 권한이 필요합니다.',
    );
  }
}

export async function mockRequest<T>(
  method: HttpMethod,
  path: string,
  options: RequestOptions,
): Promise<T> {
  const pathname = path.split('?')[0];
  authorize(method, pathname);
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
