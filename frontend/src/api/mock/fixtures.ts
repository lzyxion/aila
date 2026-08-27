/**
 * mock 모드 fixture. `VITE_USE_MOCK=true` 일 때만 쓴다.
 *
 * 설계 문서 "데모 및 테스트 환경" 의 장애 시나리오를 그대로 옮겼다 — 결제 외부 API
 * 타임아웃, DB 연결 실패, 인증 토큰 만료, Null 참조, 배포 직후 오류 증가, 비밀값 포함.
 *
 * **여기 있는 로그 라인은 전부 이미 마스킹된 형태다.** 백엔드 계약상 화면으로 나오는
 * 로그는 마스킹을 거친 값뿐이고, 원본은 DB 에 저장하지 않는다. fixture 가 원문 토큰을
 * 들고 있으면 "마스킹된 화면"이 어떤 모습인지 검증할 수 없게 되므로 여기서도 지킨다.
 */

import type {
  AnalysisJobRead,
  AnalysisResultSchema,
  CountPoint,
  ErrorGroupDetail,
  ErrorSampleRead,
  LLMConnectionRead,
  LokiConnectionRead,
  PolicyRead,
  UserRead,
} from '../types';

/** mock 데이터의 기준 시각. 모듈 로드 시점으로 고정해 화면이 흔들리지 않게 한다. */
export const NOW = new Date();

export function iso(offsetMinutes: number): string {
  return new Date(NOW.getTime() + offsetMinutes * 60_000).toISOString();
}

/**
 * 시간대별 건수 시리즈. metric 쿼리(`count_over_time`) 결과를 흉내 낸다 —
 * 로그 라인을 센 값이 아니다.
 */
export function makeSeries(
  rangeMinutes: number,
  stepSeconds: number,
  shape: (t: number) => number,
): CountPoint[] {
  const stepMinutes = stepSeconds / 60;
  const points: CountPoint[] = [];
  const steps = Math.max(1, Math.floor(rangeMinutes / stepMinutes));
  for (let i = steps; i >= 0; i -= 1) {
    const t = 1 - i / steps;
    points.push({
      timestamp: iso(-i * stepMinutes),
      value: Math.max(0, Math.round(shape(t))),
      labels: {},
    });
  }
  return points;
}

/** 배포 직후 급증 — 후반부에 몰린 모양. */
const spike = (t: number) => 4 + 46 * Math.pow(t, 4) + 6 * Math.sin(t * 11);
/** 완만하게 흐르는 배경 오류. */
const steady = (t: number) => 9 + 4 * Math.sin(t * 7) + 2 * Math.cos(t * 3);

export const seriesShapes = { spike, steady };

// ------------------------------------------------------------------ 계정

/**
 * 계정 목록 fixture (계약 1).
 *
 * 구성이 의도적이다 — **active admin 이 하나뿐**이어야 "마지막 admin 강등·비활성은 409"
 * 경로를 화면에서 실제로 눌러 볼 수 있고, 비활성 계정이 하나 있어야 재활성 버튼이
 * 빈 자리로 남지 않는다. 비밀번호는 어떤 응답에도 들어가지 않으므로 여기에도 없다.
 */
export const userSeed: UserRead[] = [
  {
    id: 1,
    username: 'admin',
    role: 'admin',
    active: true,
    created_at: iso(-60 * 24 * 30),
  },
  {
    id: 2,
    username: 'viewer',
    role: 'viewer',
    active: true,
    created_at: iso(-60 * 24 * 21),
  },
  {
    id: 3,
    username: 'oncall-watcher',
    role: 'viewer',
    active: true,
    created_at: iso(-60 * 24 * 9),
  },
  {
    id: 4,
    username: 'former-operator',
    role: 'admin',
    active: false,
    created_at: iso(-60 * 24 * 40),
  },
];

// ------------------------------------------------------------- connections

export const lokiConnectionSeed: LokiConnectionRead[] = [
  {
    id: 1,
    name: 'local-loki',
    source_type: 'loki',
    base_url: 'http://localhost:3100',
    auth_type: 'none',
    label_mapping: { app: 'service', env: 'environment' },
    active: true,
    /*
      수집 확인 대상 (Phase 7). **일부러 하나를 없는 서비스로 둔다** — `billing-api` 는
      groupSeeds 어디에도 없으므로 조회가 `ingest_absent` 경고를 남기고, 그래야 "수집 중단
      의심" 배지가 실제로 그려지는지 화면·smoke 양쪽에서 확인할 수 있다.
    */
    expected_services: ['payment-api', 'order-api', 'billing-api'],
    has_secret: false,
    created_at: iso(-60 * 24 * 12),
    updated_at: iso(-60 * 24 * 3),
  },
  {
    id: 2,
    name: 'staging-loki',
    source_type: 'loki',
    base_url: 'https://loki.staging.internal',
    auth_type: 'bearer',
    label_mapping: { service_name: 'service', deployment_env: 'environment' },
    active: true,
    // 빈 배열 = 수집 중단 확인을 하지 않는 연결. 두 상태가 화면에서 갈리는지 본다.
    expected_services: [],
    has_secret: true,
    created_at: iso(-60 * 24 * 9),
    updated_at: iso(-60 * 24 * 1),
  },
];

export const llmConnectionSeed: LLMConnectionRead[] = [
  {
    id: 1,
    name: 'Claude (기본)',
    provider: 'anthropic',
    model: 'claude-sonnet-4-6',
    base_url: null,
    is_default: true,
    active: true,
    api_key_masked: '****7f2c',
    created_at: iso(-60 * 24 * 10),
    updated_at: iso(-60 * 24 * 2),
  },
  {
    id: 2,
    name: 'GPT 백업',
    provider: 'openai',
    model: 'gpt-5.2',
    base_url: null,
    is_default: false,
    active: true,
    api_key_masked: '****a19d',
    created_at: iso(-60 * 24 * 8),
    updated_at: iso(-60 * 24 * 8),
  },
  {
    id: 3,
    name: '사내 호환 엔드포인트',
    provider: 'openai_compatible',
    model: 'qwen3-32b-instruct',
    base_url: 'http://llm-gateway.internal:8080/v1',
    is_default: false,
    active: false,
    api_key_masked: '****0b44',
    created_at: iso(-60 * 24 * 5),
    updated_at: iso(-60 * 24 * 5),
  },
];

// ----------------------------------------------------------------- policies

export const policySeed: PolicyRead[] = [
  {
    id: 1,
    loki_connection_id: 1,
    name: 'payment-api 오류 (staging)',
    description: '결제 게이트웨이 관련 ERROR 전량. 외부 API 타임아웃 감지가 주 목적.',
    logql: '{service="payment-api", environment="staging"} | json | level="ERROR"',
    /*
      분모 쿼리 (Phase 7). 오류 셀렉터와 **같은 라벨 범위**이되 level 필터가 없다 — 이게
      유입량이고, 오류 ÷ 유입량이 오류 비율이다. 정책 2·3 은 비워 둬서 "미설정이면 0 이
      아니라 `-`" 경로를 화면에서 확인할 수 있게 한다.
    */
    baseline_query: '{service="payment-api", environment="staging"}',
    default_range_minutes: 60,
    max_lines: 1000,
    exclusions: ['healthcheck', 'GET /metrics'],
    max_samples_per_group: 3,
    allow_ai_analysis: true,
    daily_analysis_limit: 20,
    active: true,
    // 스케줄 + 신규 그룹 자동 분석이 **둘 다 켜진** 정책이 하나는 있어야 배지·경고 문구와
    // 이력의 "자동" 배지를 화면에서 확인할 수 있다.
    schedule_enabled: true,
    schedule_interval_minutes: 60,
    auto_analyze_new: true,
    created_at: iso(-60 * 24 * 11),
    updated_at: iso(-60 * 24 * 2),
  },
  {
    id: 2,
    loki_connection_id: 1,
    name: '전체 서비스 ERROR/FATAL',
    description: '넓게 훑는 정책. 라인 상한을 낮게 잡아 비용을 막는다.',
    logql: '{environment="staging"} | json | level=~"ERROR|FATAL"',
    baseline_query: null,
    default_range_minutes: 180,
    max_lines: 500,
    exclusions: [],
    max_samples_per_group: 2,
    allow_ai_analysis: true,
    daily_analysis_limit: null,
    active: true,
    // 스케줄만 켜고 자동 분석은 끈 정책 — 조회는 자동, 분석은 사람이 판단하는 조합.
    schedule_enabled: true,
    schedule_interval_minutes: 360,
    auto_analyze_new: false,
    created_at: iso(-60 * 24 * 7),
    updated_at: iso(-60 * 24 * 7),
  },
  {
    id: 3,
    loki_connection_id: 2,
    name: 'auth-api 인증 실패 (보관)',
    description: '토큰 만료 급증 조사용. 지금은 비활성.',
    logql: '{service="auth-api"} | json | level="ERROR" | line_format "{{.msg}}"',
    baseline_query: null,
    default_range_minutes: 360,
    max_lines: 2000,
    exclusions: ['expected-expiry'],
    max_samples_per_group: 3,
    allow_ai_analysis: false,
    daily_analysis_limit: 5,
    active: false,
    schedule_enabled: false,
    schedule_interval_minutes: null,
    auto_analyze_new: false,
    created_at: iso(-60 * 24 * 20),
    updated_at: iso(-60 * 24 * 6),
  },
];

// ------------------------------------------------------------- error groups

interface GroupSeed {
  id: number;
  fingerprint: string;
  service: string;
  environment: string;
  error_type: string;
  normalized_message: string;
  count: number;
  first_seen_min: number;
  last_seen_min: number;
  labels: Record<string, string>;
  top_stack_frame: string | null;
  samples: Array<{ min: number; masked_log: string; stacktrace?: string }>;
  shape: (t: number) => number;
}

/**
 * 대표 로그는 마스킹 이후 형태로만 존재한다. 플레이스홀더는 백엔드가 실제로 넣는
 * 형식 `<MASKED:종류>` 그대로 쓴다 (`app/masking/rules.py` 의 KINDS) — 표식이 다르면
 * 화면·복사·보고서에서 "마스킹된 자리"를 알아보는 눈이 mock 과 실제에서 갈린다.
 */
export const groupSeeds: GroupSeed[] = [
  {
    id: 101,
    fingerprint: 'fp_9c1a4e77b2d0',
    service: 'payment-api',
    environment: 'staging',
    error_type: 'TimeoutError',
    normalized_message: 'payment gateway request timed out after <NUM>ms (upstream <NUM>)',
    count: 412,
    first_seen_min: -58,
    last_seen_min: -1,
    labels: {
      service: 'payment-api',
      environment: 'staging',
      level: 'ERROR',
      release: '2026.08.24-3',
      pod: 'payment-api-7d9c4b-xk2lp',
    },
    top_stack_frame: 'app/gateway/psp_client.py:142 in charge()',
    samples: [
      {
        min: -3,
        masked_log:
          '{"ts":"<TIMESTAMP>","level":"ERROR","service":"payment-api","msg":"payment gateway request timed out after 30000ms (upstream 504)","request_id":"<UUID>","user":"<MASKED:EMAIL>","authorization":"<MASKED:BEARER_TOKEN>"}',
        stacktrace:
          'Traceback (most recent call last):\n  File "app/gateway/psp_client.py", line 142, in charge\n    resp = await self._http.post(url, json=payload, timeout=30.0)\n  File "httpx/_client.py", line 1878, in post\n    return await self.request(...)\nhttpx.ReadTimeout: timed out',
      },
      {
        min: -12,
        masked_log:
          '{"ts":"<TIMESTAMP>","level":"ERROR","service":"payment-api","msg":"payment gateway request timed out after 30000ms (upstream 504)","request_id":"<UUID>","card":"<MASKED:CARD>"}',
      },
      {
        min: -27,
        masked_log:
          '{"ts":"<TIMESTAMP>","level":"ERROR","service":"payment-api","msg":"payment gateway request timed out after 28500ms (upstream 504)","request_id":"<UUID>","client_ip":"<IP>"}',
      },
    ],
    shape: spike,
  },
  {
    id: 102,
    fingerprint: 'fp_3b77d0f81ace',
    service: 'order-api',
    environment: 'staging',
    error_type: 'DatabaseConnectionError',
    normalized_message: 'could not connect to database: connection pool exhausted (<NUM>/<NUM>)',
    count: 186,
    first_seen_min: -55,
    last_seen_min: -2,
    labels: {
      service: 'order-api',
      environment: 'staging',
      level: 'ERROR',
      release: '2026.08.22-1',
    },
    top_stack_frame: 'app/db/session.py:61 in get_session()',
    samples: [
      {
        min: -6,
        masked_log:
          '{"ts":"<TIMESTAMP>","level":"ERROR","service":"order-api","msg":"could not connect to database: connection pool exhausted (20/20)","dsn":"<MASKED:DB_URI>","request_id":"<UUID>"}',
        stacktrace:
          'Traceback (most recent call last):\n  File "app/db/session.py", line 61, in get_session\n    conn = await pool.acquire(timeout=5)\nasyncpg.exceptions.TooManyConnectionsError: connection pool exhausted',
      },
      {
        min: -19,
        masked_log:
          '{"ts":"<TIMESTAMP>","level":"ERROR","service":"order-api","msg":"could not connect to database: connection pool exhausted (20/20)","dsn":"<MASKED:DB_URI>","request_id":"<UUID>"}',
      },
    ],
    shape: steady,
  },
  {
    id: 103,
    fingerprint: 'fp_5e02aa9134cd',
    service: 'auth-api',
    environment: 'staging',
    error_type: 'JWTExpiredError',
    normalized_message: 'token validation failed: JWT expired at <TIMESTAMP> (401)',
    count: 143,
    first_seen_min: -59,
    last_seen_min: -1,
    labels: {
      service: 'auth-api',
      environment: 'staging',
      level: 'ERROR',
      release: '2026.08.20-2',
    },
    top_stack_frame: 'app/security/jwt.py:88 in decode_token()',
    samples: [
      {
        min: -4,
        masked_log:
          '{"ts":"<TIMESTAMP>","level":"ERROR","service":"auth-api","msg":"token validation failed: JWT expired at <TIMESTAMP>","status":401,"token":"<MASKED:JWT>","subject":"<MASKED:EMAIL>"}',
      },
      {
        min: -21,
        masked_log:
          '{"ts":"<TIMESTAMP>","level":"ERROR","service":"auth-api","msg":"token validation failed: JWT expired at <TIMESTAMP>","status":401,"token":"<MASKED:JWT>"}',
      },
    ],
    shape: steady,
  },
  {
    id: 104,
    fingerprint: 'fp_7fa1cc02be34',
    service: 'user-api',
    environment: 'staging',
    error_type: 'AttributeError',
    normalized_message: "'NoneType' object has no attribute 'preferred_locale'",
    count: 77,
    first_seen_min: -47,
    last_seen_min: -8,
    labels: {
      service: 'user-api',
      environment: 'staging',
      level: 'ERROR',
      release: '2026.08.24-3',
    },
    top_stack_frame: 'app/profile/serializer.py:34 in to_dict()',
    samples: [
      {
        min: -8,
        masked_log:
          "{\"ts\":\"<TIMESTAMP>\",\"level\":\"ERROR\",\"service\":\"user-api\",\"msg\":\"'NoneType' object has no attribute 'preferred_locale'\",\"user_id\":\"<UUID>\"}",
        stacktrace:
          'Traceback (most recent call last):\n  File "app/profile/serializer.py", line 34, in to_dict\n    "locale": profile.settings.preferred_locale,\nAttributeError: \'NoneType\' object has no attribute \'preferred_locale\'',
      },
    ],
    shape: spike,
  },
  {
    id: 105,
    fingerprint: 'fp_a4d9016fe7b1',
    service: 'notification-api',
    environment: 'staging',
    error_type: 'HTTPError',
    normalized_message: 'webhook delivery failed with status <NUM> after <NUM> retries',
    count: 54,
    first_seen_min: -51,
    last_seen_min: -5,
    labels: {
      service: 'notification-api',
      environment: 'staging',
      level: 'ERROR',
      release: '2026.08.23-1',
    },
    top_stack_frame: 'app/webhook/dispatch.py:97 in deliver()',
    samples: [
      {
        min: -5,
        masked_log:
          '{"ts":"<TIMESTAMP>","level":"ERROR","service":"notification-api","msg":"webhook delivery failed with status 502 after 5 retries","target":"https://hooks.example.com/t/<MASKED:API_KEY>","contact":"<MASKED:PHONE>"}',
      },
    ],
    shape: steady,
  },
  {
    id: 106,
    fingerprint: 'fp_c8801b73aa5f',
    service: 'payment-api',
    environment: 'staging',
    error_type: 'ValidationError',
    normalized_message: 'invalid currency code <STR> for merchant <UUID>',
    count: 31,
    first_seen_min: -44,
    last_seen_min: -14,
    labels: {
      service: 'payment-api',
      environment: 'staging',
      level: 'ERROR',
      release: '2026.08.24-3',
    },
    top_stack_frame: 'app/api/charges.py:58 in create_charge()',
    samples: [
      {
        min: -14,
        masked_log:
          '{"ts":"<TIMESTAMP>","level":"ERROR","service":"payment-api","msg":"invalid currency code \\"KRW \\" for merchant <UUID>","request_id":"<UUID>"}',
      },
    ],
    shape: steady,
  },
];

export function buildSamples(seed: GroupSeed): ErrorSampleRead[] {
  return seed.samples.map((sample, index) => ({
    id: seed.id * 10 + index,
    occurred_at: iso(sample.min),
    masked_log: sample.masked_log,
    labels: seed.labels,
    stacktrace: sample.stacktrace ?? null,
    masking_rule_version: 'v2',
  }));
}

export function buildGroupDetail(seed: GroupSeed, queryRunId: number): ErrorGroupDetail {
  return {
    id: seed.id,
    query_run_id: queryRunId,
    fingerprint: seed.fingerprint,
    service: seed.service,
    environment: seed.environment,
    error_type: seed.error_type,
    normalized_message: seed.normalized_message,
    count: seed.count,
    first_seen: iso(seed.first_seen_min),
    last_seen: iso(seed.last_seen_min),
    analysis_status: null,
    latest_analysis_job_id: null,
    latest_severity: null,
    labels: seed.labels,
    top_stack_frame: seed.top_stack_frame,
    normalization_rule_version: 'v2',
    samples: buildSamples(seed),
    trend: makeSeries(60, 300, seed.shape),
    analyses: [],
  };
}

// --------------------------------------------------------- 분석 결과 fixture

/** fingerprint 별 LLM 분석 결과. 모두 가설·한계를 갖춘 구조화 응답이다. */
export const analysisResultByFingerprint: Record<string, AnalysisResultSchema> = {
  fp_9c1a4e77b2d0: {
    summary: '외부 결제 게이트웨이 응답 지연으로 30 초 타임아웃이 반복되고 있습니다.',
    severity: 'high',
    hypotheses: [
      {
        cause: '외부 PSP(결제 대행사) API 자체의 지연 또는 부분 장애',
        confidence: 0.78,
        evidence: ['upstream 504', 'httpx.ReadTimeout', '동일 엔드포인트에서만 발생'],
      },
      {
        cause: '2026.08.24-3 배포에서 늘어난 동시 요청이 커넥션 풀을 고갈시킴',
        confidence: 0.44,
        evidence: ['release=2026.08.24-3 이후 급증', '건수 곡선이 배포 시점에 꺾임'],
      },
      {
        cause: '스테이징 네트워크(egress NAT) 구간의 패킷 손실',
        confidence: 0.21,
        evidence: ['단일 pod 에 치우치지 않고 전 pod 에서 발생'],
      },
    ],
    investigation_steps: [
      'PSP 상태 페이지와 최근 공지 확인',
      '같은 시간대 PSP 호출 성공/실패 비율과 p99 지연 확인',
      '2026.08.24-3 배포의 타임아웃·재시도 설정 변경 여부 확인',
      'egress 구간 패킷 손실률 확인 (다른 외부 호출도 함께 느린지)',
    ],
    mitigation: [
      '타임아웃을 30 초에서 10 초로 낮추고 지수 백오프 재시도 2 회로 제한',
      'PSP 호출에 서킷 브레이커 적용 — 연속 실패 시 즉시 실패로 전환',
      '결제 요청 큐잉 후 비동기 확정 처리로 사용자 대기 분리',
    ],
    limitations: [
      '로그만으로는 외부 PSP 장애인지 자사 네트워크 문제인지 확정할 수 없습니다.',
      '대표 로그 3 개는 마스킹된 값이라 실제 요청 페이로드 차이를 비교하지 못했습니다.',
      '건수 추이는 metric 쿼리 기준이며 개별 요청의 지연 분포는 포함하지 않았습니다.',
    ],
  },
  fp_3b77d0f81ace: {
    summary: 'order-api 의 DB 커넥션 풀이 상한 20 에 도달해 연결 획득이 실패하고 있습니다.',
    severity: 'high',
    hypotheses: [
      {
        cause: '커넥션을 반환하지 않는 경로(누수)가 있어 풀이 서서히 고갈됨',
        confidence: 0.62,
        evidence: ['20/20 로 정확히 상한에서 실패', '시간이 지나도 회복되지 않음'],
      },
      {
        cause: '장기 실행 쿼리가 커넥션을 점유해 대기가 누적됨',
        confidence: 0.5,
        evidence: ['acquire(timeout=5) 에서 실패', '오류가 균일하게 지속됨'],
      },
    ],
    investigation_steps: [
      'PostgreSQL `pg_stat_activity` 에서 idle in transaction 세션 확인',
      '풀 크기(20)와 워커 수·동시 요청 수의 비율 재계산',
      '최근 추가된 트랜잭션 경로에서 커밋/롤백 누락 확인',
    ],
    mitigation: [
      '세션을 컨텍스트 매니저로만 획득하도록 강제하고 누수 경로 제거',
      'statement_timeout 과 idle_in_transaction_session_timeout 설정',
      '임시로 풀 크기 상향 — 근본 원인 확인 전까지의 완화책일 뿐',
    ],
    limitations: [
      '애플리케이션 로그만 있어 DB 서버 쪽 세션 상태는 확인하지 못했습니다.',
      '누수 위치를 특정하려면 커넥션 획득/반환 추적 로그가 추가로 필요합니다.',
    ],
  },
  fp_7fa1cc02be34: {
    summary: '프로필 직렬화에서 settings 가 없는 사용자에 대해 None 참조가 발생합니다.',
    severity: 'medium',
    hypotheses: [
      {
        cause: '신규 가입 사용자에게 settings 행이 생성되지 않아 관계가 None 인 상태',
        confidence: 0.71,
        evidence: ["'NoneType' object has no attribute 'preferred_locale'", 'serializer.py:34'],
      },
      {
        cause: '2026.08.24-3 에서 추가된 locale 필드가 기존 데이터에 백필되지 않음',
        confidence: 0.55,
        evidence: ['release=2026.08.24-3 이후에만 발생'],
      },
    ],
    investigation_steps: [
      'settings 가 NULL 인 프로필 행 수 확인',
      '해당 배포의 마이그레이션에 백필이 포함됐는지 확인',
    ],
    mitigation: [
      '직렬화에서 기본 locale 로 폴백',
      '누락된 settings 행 백필 마이그레이션 실행',
    ],
    limitations: ['영향받은 사용자 수는 로그 건수(77)의 하한일 뿐 실제 규모와 다를 수 있습니다.'],
  },
};

/** 이미 끝난 분석 이력 (fingerprint 기준으로 그룹에 조인된다). */
export const analysisJobSeed: AnalysisJobRead[] = [
  {
    id: 9001,
    error_group_id: 101,
    llm_connection_id: 1,
    fingerprint: 'fp_9c1a4e77b2d0',
    status: 'succeeded',
    provider: 'anthropic',
    model: 'claude-sonnet-4-6',
    prompt_version: 'v1',
    requested_at: iso(-180),
    // 스케줄의 자동 분석으로 실행된 이력 — 화면의 "자동" 배지 경로.
    triggered_by: 'schedule',
    started_at: iso(-180),
    completed_at: iso(-179),
    error_message: null,
    result: analysisResultByFingerprint.fp_9c1a4e77b2d0,
    usage: {
      provider: 'anthropic',
      model: 'claude-sonnet-4-6',
      input_tokens: 3142,
      output_tokens: 812,
      estimated_cost: '0.0216',
      // 계산에 실제로 쓴 단가를 복사해 둔다 (단가표가 나중에 바뀌어도 이력은 그대로).
      pricing_snapshot: { input_per_1k: 0.003, output_per_1k: 0.015, currency: 'USD' },
      latency_ms: 8420,
      status: 'succeeded',
      failure_reason: null,
    },
  },
  {
    id: 9002,
    error_group_id: 102,
    llm_connection_id: 1,
    fingerprint: 'fp_3b77d0f81ace',
    status: 'succeeded',
    provider: 'anthropic',
    model: 'claude-sonnet-4-6',
    prompt_version: 'v1',
    requested_at: iso(-1450),
    started_at: iso(-1450),
    completed_at: iso(-1449),
    error_message: null,
    result: analysisResultByFingerprint.fp_3b77d0f81ace,
    usage: {
      provider: 'anthropic',
      model: 'claude-sonnet-4-6',
      input_tokens: 2680,
      output_tokens: 645,
      estimated_cost: '0.0177',
      pricing_snapshot: { input_per_1k: 0.003, output_per_1k: 0.015, currency: 'USD' },
      latency_ms: 7310,
      status: 'succeeded',
      failure_reason: null,
    },
  },
  {
    id: 9003,
    error_group_id: 104,
    llm_connection_id: 2,
    fingerprint: 'fp_7fa1cc02be34',
    status: 'succeeded',
    provider: 'openai',
    model: 'gpt-5.2',
    prompt_version: 'v1',
    requested_at: iso(-620),
    triggered_by: 'manual',
    started_at: iso(-620),
    completed_at: iso(-619),
    error_message: null,
    result: analysisResultByFingerprint.fp_7fa1cc02be34,
    usage: {
      provider: 'openai',
      model: 'gpt-5.2',
      input_tokens: 2104,
      output_tokens: 498,
      // gpt-5.2 는 단가표에 없다 — 값은 0 이 아니라 null 이고, 화면은 `-` 로 쓴다.
      // (사용량 화면의 "단가 등록" 인라인 UI 가 이 상태를 대상으로 한다.)
      estimated_cost: null,
      pricing_snapshot: null,
      latency_ms: 11240,
      status: 'succeeded',
      failure_reason: null,
    },
  },
  {
    id: 9004,
    error_group_id: 105,
    llm_connection_id: 2,
    fingerprint: 'fp_a4d9016fe7b1',
    status: 'failed',
    provider: 'openai',
    model: 'gpt-5.2',
    prompt_version: 'v1',
    requested_at: iso(-300),
    triggered_by: 'manual',
    started_at: iso(-300),
    completed_at: iso(-299),
    error_message: '구조화 응답 검증 실패: limitations 가 비어 있습니다 (최소 1 개 필요).',
    result: null,
    usage: {
      provider: 'openai',
      model: 'gpt-5.2',
      input_tokens: 1890,
      output_tokens: 220,
      estimated_cost: null,
      pricing_snapshot: null,
      latency_ms: 5980,
      status: 'failed',
      failure_reason: 'schema_validation_error',
    },
  },
];

/*
  일별 사용량 차트가 막대 하나짜리가 되지 않도록 지난 며칠에 걸친 이력을 더 둔다.

  **fingerprint 는 위에서 이미 분석된 것만 재사용한다** — 미분석 그룹(103·106)에 이력을
  붙이면 "미분석 신규 그룹" 이 0 이 되어 통합 대시보드의 핵심 숫자를 확인할 수 없게 된다.
*/
analysisJobSeed.push(
  ...[
    { id: 9011, group: 101, fp: 'fp_9c1a4e77b2d0', days: 2, input: 2980, output: 720 },
    { id: 9012, group: 102, fp: 'fp_3b77d0f81ace', days: 3, input: 2510, output: 604 },
    { id: 9013, group: 101, fp: 'fp_9c1a4e77b2d0', days: 4, input: 3320, output: 845 },
    { id: 9014, group: 104, fp: 'fp_7fa1cc02be34', days: 5, input: 2240, output: 512 },
  ].map<AnalysisJobRead>((row) => ({
    id: row.id,
    error_group_id: row.group,
    llm_connection_id: 1,
    fingerprint: row.fp,
    status: 'succeeded',
    provider: 'anthropic',
    model: 'claude-sonnet-4-6',
    prompt_version: 'v1',
    requested_at: iso(-60 * 24 * row.days - 90),
    triggered_by: row.days % 2 === 0 ? 'schedule' : 'manual',
    started_at: iso(-60 * 24 * row.days - 90),
    completed_at: iso(-60 * 24 * row.days - 89),
    error_message: null,
    result: analysisResultByFingerprint[row.fp] ?? null,
    usage: {
      provider: 'anthropic',
      model: 'claude-sonnet-4-6',
      input_tokens: row.input,
      output_tokens: row.output,
      estimated_cost: ((row.input / 1000) * 0.003 + (row.output / 1000) * 0.015).toFixed(4),
      pricing_snapshot: { input_per_1k: 0.003, output_per_1k: 0.015, currency: 'USD' },
      latency_ms: 6800 + row.id % 900,
      status: 'succeeded',
      failure_reason: null,
    },
  })),
);

/** 새 분석을 실행했을 때 돌려줄 기본 결과 (fingerprint 별 fixture 가 없을 때). */
export const fallbackAnalysisResult: AnalysisResultSchema = {
  summary: '반복되는 오류이지만 대표 로그만으로는 단일 원인을 특정하기 어렵습니다.',
  severity: 'medium',
  hypotheses: [
    {
      cause: '최근 배포로 들어간 변경이 특정 입력 경로에서 실패를 유발',
      confidence: 0.48,
      evidence: ['같은 release 라벨에 집중', '동일한 정규화 메시지 반복'],
    },
    {
      cause: '외부 의존 서비스의 간헐적 응답 실패',
      confidence: 0.32,
      evidence: ['시간대별로 고르게 분포'],
    },
  ],
  investigation_steps: [
    '해당 release 의 변경 목록 확인',
    '같은 시간대 의존 서비스의 오류율 확인',
    'Loki 에서 이 그룹의 라벨·시각으로 원본 로그 재조회',
  ],
  mitigation: ['재시도·타임아웃 설정 점검', '실패 입력에 대한 방어적 검증 추가'],
  limitations: [
    '마스킹된 대표 로그 몇 건만 보고 추정한 결과입니다.',
    '원인을 확정하려면 원본 로그와 의존 서비스 지표를 함께 확인해야 합니다.',
  ],
};
