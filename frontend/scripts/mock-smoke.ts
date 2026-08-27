/**
 * mock 계층 스모크 테스트 (개발용, 앱 번들에 포함되지 않는다).
 *
 *   npx vite build --ssr scripts/mock-smoke.ts --outDir <out>
 *   node <out>/mock-smoke.js
 *
 * 확인 대상: 인증·역할, 라우팅, 분석 작업의 pending -> running -> succeeded 전이,
 * 멱등 재사용, fingerprint 기준 분석 상태, 스케줄 필드, 통합 대시보드 요약, 보고서 렌더링.
 */

import { ApiError } from '../src/api/client';
import { mockRequest } from '../src/api/mock/handler';
import type {
  AnalysisJobCreateResponse,
  AnalysisJobListResponse,
  AnalysisJobRead,
  AppSettingRead,
  AuthUser,
  DailyLimitResponse,
  DashboardErrorGroupsResponse,
  DashboardOverviewResponse,
  DashboardSummaryResponse,
  ErrorGroupDetail,
  ErrorGroupListResponse,
  LLMModelListResponse,
  LokiConnectionRead,
  PolicyPreviewResponse,
  PolicyRead,
  QueryRunListResponse,
  QueryRunRead,
  UsageResponse,
  UserListResponse,
  UserRead,
} from '../src/api/types';
import { asModelPricingTable } from '../src/api/types';

let failures = 0;
function check(label: string, condition: boolean, detail = ''): void {
  if (condition) {
    console.log(`  ok   ${label}`);
  } else {
    failures += 1;
    console.log(`  FAIL ${label} ${detail}`);
  }
}
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** 실패 경로도 계약이다 — 화면의 폴백은 이 상태 코드를 보고 갈린다. */
async function expectStatus(call: () => Promise<unknown>, status: number): Promise<boolean> {
  try {
    await call();
    return false;
  } catch (error) {
    return error instanceof ApiError && error.status === status;
  }
}

/** admin 세션으로 되돌린다 — viewer 시나리오 뒤에 나머지 단언이 이어지므로. */
async function loginAsAdmin(): Promise<void> {
  await mockRequest<AuthUser>('POST', '/api/auth/login', {
    body: { username: 'admin', password: 'admin' },
  });
}

async function main(): Promise<void> {
  console.log('auth (계약 — 세션·역할)');
  // 로그인 전에는 데이터 경로가 전부 401 이다. 화면은 이 401 을 가로채 /login 으로 간다.
  check(
    '미인증 요청은 401',
    await expectStatus(() => mockRequest('GET', '/api/policies', {}), 401),
  );
  check(
    '미인증이면 /api/auth/me 도 401',
    await expectStatus(() => mockRequest('GET', '/api/auth/me', {}), 401),
  );
  check(
    '잘못된 자격 증명은 401',
    await expectStatus(
      () =>
        mockRequest('POST', '/api/auth/login', {
          body: { username: 'admin', password: 'wrong' },
        }),
      401,
    ),
  );

  const admin = await mockRequest<AuthUser>('POST', '/api/auth/login', {
    body: { username: 'admin', password: 'admin' },
  });
  check('로그인 응답 {username, role}', admin.username === 'admin' && admin.role === 'admin');
  const me = await mockRequest<AuthUser>('GET', '/api/auth/me', {});
  check('로그인 후 me 가 200', me.username === 'admin' && me.role === 'admin');
  check(
    '로그인 후 데이터 경로가 열린다',
    (await mockRequest<PolicyRead[]>('GET', '/api/policies', {})).length > 0,
  );

  // viewer 는 GET 만 — 판정은 서버(여기서는 mock)가 한다. 화면이 버튼을 감추는 것은 편의다.
  const viewer = await mockRequest<AuthUser>('POST', '/api/auth/login', {
    body: { username: 'viewer', password: 'viewer' },
  });
  check('viewer 계정의 role', viewer.role === 'viewer');
  check(
    'viewer 의 GET 은 통과',
    (await mockRequest<PolicyRead[]>('GET', '/api/policies', {})).length > 0,
  );
  check(
    'viewer 의 정책 생성은 403',
    await expectStatus(
      () =>
        mockRequest('POST', '/api/policies', {
          body: {
            loki_connection_id: 1,
            name: 'viewer-should-fail',
            logql: '{service="payment-api"}',
            default_range_minutes: 30,
            max_lines: 100,
            exclusions: [],
            max_samples_per_group: 2,
            allow_ai_analysis: true,
            daily_analysis_limit: null,
          },
        }),
      403,
    ),
  );
  check(
    'viewer 의 정책 실행(POST query-run)은 403',
    await expectStatus(() => mockRequest('POST', '/api/policies/1/query-runs', { body: {} }), 403),
  );
  check(
    'viewer 의 AI 분석 실행은 403',
    await expectStatus(
      () => mockRequest('POST', '/api/error-groups/103/analysis-jobs', { body: {} }),
      403,
    ),
  );
  check(
    'viewer 의 설정 변경(PUT)은 403',
    await expectStatus(
      () => mockRequest('PUT', '/api/settings/daily_analysis_limit', { body: { value: 10 } }),
      403,
    ),
  );
  check(
    'viewer 의 비활성화(DELETE)는 403',
    await expectStatus(() => mockRequest('DELETE', '/api/policies/2', {}), 403),
  );
  // 계정 관리는 **GET 도** admin 전용이다 — `/api/auth/` 접두사로 통째로 열어 두면
  // 계정 목록이 viewer(나아가 미인증)에게 새어 나간다.
  check(
    'viewer 의 계정 목록 조회는 403',
    await expectStatus(() => mockRequest('GET', '/api/auth/users', {}), 403),
  );

  await mockRequest<void>('POST', '/api/auth/logout', {});
  check(
    '로그아웃하면 다시 401',
    await expectStatus(() => mockRequest('GET', '/api/policies', {}), 401),
  );
  check(
    '미인증이면 계정 목록도 401',
    await expectStatus(() => mockRequest('GET', '/api/auth/users', {}), 401),
  );
  await loginAsAdmin();

  console.log('계정 관리 (계약 1 — admin 전용)');
  const userList = await mockRequest<UserListResponse>('GET', '/api/auth/users', {});
  check('봉투 {total, items}', typeof userList.total === 'number' && Array.isArray(userList.items));
  check(
    '항목 모양 {id, username, role, active, created_at}',
    userList.items.every(
      (row) =>
        typeof row.id === 'number' &&
        typeof row.username === 'string' &&
        (row.role === 'admin' || row.role === 'viewer') &&
        typeof row.active === 'boolean' &&
        typeof row.created_at === 'string',
    ),
  );
  check(
    '비밀번호는 어떤 형태로도 실리지 않는다',
    !JSON.stringify(userList).toLowerCase().includes('password'),
  );
  check('비활성 계정도 목록에 남는다', userList.items.some((row) => !row.active));

  const createdUser = await mockRequest<UserRead>('POST', '/api/auth/users', {
    body: { username: 'smoke-viewer', password: 'smoke-pass', role: 'viewer' },
  });
  check('계정 생성', typeof createdUser.username === 'string' && createdUser.role === 'viewer');
  check(
    '같은 사용자명은 409',
    await expectStatus(
      () =>
        mockRequest('POST', '/api/auth/users', {
          body: { username: 'smoke-viewer', password: 'x1234', role: 'viewer' },
        }),
      409,
    ),
  );

  const promoted = await mockRequest<UserRead>('PATCH', `/api/auth/users/${createdUser.id}`, {
    body: { role: 'admin' },
  });
  check('역할 변경이 반영된다', promoted.role === 'admin');
  const demoted = await mockRequest<UserRead>('PATCH', `/api/auth/users/${createdUser.id}`, {
    body: { role: 'viewer' },
  });
  check('되돌리기도 된다', demoted.role === 'viewer');

  // 마지막 남은 활성 admin 보호 — 이게 없으면 admin 을 전부 강등한 순간 아무도 되돌릴 수
  // 없는 상태가 되고, 화면에서는 성공으로 보인다.
  const adminRow = userList.items.find((row) => row.username === 'admin')!;
  check(
    '마지막 활성 admin 의 강등은 409',
    await expectStatus(
      () => mockRequest('PATCH', `/api/auth/users/${adminRow.id}`, { body: { role: 'viewer' } }),
      409,
    ),
  );
  check(
    '마지막 활성 admin 의 비활성은 409',
    await expectStatus(
      () => mockRequest('PATCH', `/api/auth/users/${adminRow.id}`, { body: { active: false } }),
      409,
    ),
  );
  check(
    '자기 자신 비활성(DELETE)은 409',
    await expectStatus(() => mockRequest('DELETE', `/api/auth/users/${adminRow.id}`, {}), 409),
  );

  // DELETE 는 실삭제가 아니라 active=false 다.
  await mockRequest<void>('DELETE', `/api/auth/users/${createdUser.id}`, {});
  const afterDelete = await mockRequest<UserListResponse>('GET', '/api/auth/users', {});
  check(
    'DELETE 는 삭제가 아니라 비활성화',
    afterDelete.items.some((row) => row.id === createdUser.id && !row.active),
  );
  // 비활성 계정은 로그인할 수 없고, 그 사실을 401 로만 알린다(계정 열거 방지).
  check(
    '비활성 계정은 로그인 401',
    await expectStatus(
      () =>
        mockRequest('POST', '/api/auth/login', {
          body: { username: 'smoke-viewer', password: 'smoke-pass' },
        }),
      401,
    ),
  );
  await loginAsAdmin();

  // 비밀번호를 바꾸면 그 계정의 세션이 전부 무효화된다 (계약).
  const viewerRow = afterDelete.items.find((row) => row.username === 'viewer')!;
  await mockRequest<AuthUser>('POST', '/api/auth/login', {
    body: { username: 'viewer', password: 'viewer' },
  });
  await loginAsAdmin();
  await mockRequest<UserRead>('PATCH', `/api/auth/users/${viewerRow.id}`, {
    body: { password: 'rotated-1' },
  });
  check(
    '비밀번호를 바꾸면 예전 비밀번호로 로그인할 수 없다',
    await expectStatus(
      () =>
        mockRequest('POST', '/api/auth/login', {
          body: { username: 'viewer', password: 'viewer' },
        }),
      401,
    ),
  );
  const rotated = await mockRequest<AuthUser>('POST', '/api/auth/login', {
    body: { username: 'viewer', password: 'rotated-1' },
  });
  check('새 비밀번호로는 로그인된다', rotated.username === 'viewer');
  await loginAsAdmin();
  // 뒤 단언들이 기존 비밀번호를 쓰므로 되돌려 놓는다.
  await mockRequest<UserRead>('PATCH', `/api/auth/users/${viewerRow.id}`, {
    body: { password: 'viewer' },
  });
  await loginAsAdmin();

  console.log('dashboard summary (계약 3 — 통합 대시보드)');
  const summary = await mockRequest<DashboardSummaryResponse>(
    'GET',
    '/api/dashboard/summary',
    {},
  );
  check('generated_at 존재', typeof summary.generated_at === 'string');
  check('정책 카드가 정책 수만큼', summary.policies.length === 3);
  check(
    '카드 모양이 계약대로',
    summary.policies.every(
      (policy) =>
        typeof policy.policy_id === 'number' &&
        typeof policy.name === 'string' &&
        typeof policy.active === 'boolean' &&
        typeof policy.schedule_enabled === 'boolean' &&
        (policy.schedule_interval_minutes === null ||
          typeof policy.schedule_interval_minutes === 'number') &&
        typeof policy.unanalyzed_group_count === 'number' &&
        Array.isArray(policy.warnings),
    ),
  );
  check(
    'last_run 은 {id, started_at, status, fetched_count, group_count, warnings} 또는 null',
    summary.policies.every(
      (policy) =>
        policy.last_run === null ||
        (typeof policy.last_run.id === 'number' &&
          typeof policy.last_run.started_at === 'string' &&
          typeof policy.last_run.status === 'string' &&
          typeof policy.last_run.fetched_count === 'number' &&
          typeof policy.last_run.group_count === 'number' &&
          Array.isArray(policy.last_run.warnings)),
    ),
  );
  // metric 실패는 0 이 아니라 null 이다 — 화면이 `-` 로 그리는 경로.
  check(
    'metric 실패 정책의 total_errors_24h 는 null',
    summary.policies.some((policy) => policy.total_errors_24h === null),
  );
  check(
    '나머지 정책의 24h 건수는 숫자',
    summary.policies.some((policy) => typeof policy.total_errors_24h === 'number'),
  );
  check(
    '미분석 신규 그룹 수가 0 보다 큰 정책이 있다',
    summary.policies.some((policy) => policy.unanalyzed_group_count > 0),
  );
  check(
    '경고는 {code, message} 형식',
    summary.policies
      .flatMap((policy) => policy.warnings)
      .every((warning) => typeof warning.code === 'string' && typeof warning.message === 'string'),
  );
  check(
    '기존 overview 는 그대로 남아 있다 (정책 상세 뷰용)',
    (await mockRequest<DashboardOverviewResponse>('GET', '/api/dashboard/overview', {}))
      .series.length > 0,
  );

  // 계약 5 — 카드 스파크라인. 추가 Loki 호출 없이 **같은 count_over_time 결과**를 재사용하므로
  // 시리즈 합계와 total_errors_24h 가 어긋나면 두 값이 다른 출처라는 뜻이다.
  check(
    'series_24h 가 실린다',
    summary.policies.every((policy) => Array.isArray(policy.series_24h)),
  );
  check(
    'series_24h 포인트 모양 {timestamp, value}',
    summary.policies
      .flatMap((policy) => policy.series_24h ?? [])
      .every((point) => typeof point.timestamp === 'string' && typeof point.value === 'number'),
  );
  check(
    'series_24h 합계가 total_errors_24h 와 일치 (같은 결과 재사용)',
    summary.policies
      .filter((policy) => policy.total_errors_24h !== null)
      .every(
        (policy) =>
          (policy.series_24h ?? []).reduce((acc, point) => acc + point.value, 0) ===
          policy.total_errors_24h,
      ),
  );
  check(
    'metric 실패 정책의 series_24h 는 빈 배열 (평평한 0 선을 그리지 않는다)',
    summary.policies
      .filter((policy) => policy.total_errors_24h === null)
      .every((policy) => (policy.series_24h ?? []).length === 0),
  );
  // step 3600 = 24 시간이면 25 포인트(양 끝 포함).
  check(
    'series_24h 는 시간당 포인트 (step 3600)',
    summary.policies
      .filter((policy) => (policy.series_24h ?? []).length > 0)
      .every((policy) => (policy.series_24h ?? []).length === 25),
  );

  console.log('전체 오류 그룹 (계약 4)');
  const allGroups = await mockRequest<DashboardErrorGroupsResponse>(
    'GET',
    '/api/dashboard/error-groups',
    { query: { limit: 20, offset: 0 } },
  );
  check(
    '봉투 {total, limit, offset, items}',
    typeof allGroups.total === 'number' &&
      typeof allGroups.limit === 'number' &&
      typeof allGroups.offset === 'number' &&
      Array.isArray(allGroups.items),
  );
  check(
    '항목에 policy_id·policy_name 이 붙는다',
    allGroups.items.length > 0 &&
      allGroups.items.every(
        (item) => typeof item.policy_id === 'number' && typeof item.policy_name === 'string',
      ),
  );
  check(
    '항목이 ErrorGroupSummary 를 그대로 포함한다',
    allGroups.items.every(
      (item) =>
        typeof item.id === 'number' &&
        typeof item.fingerprint === 'string' &&
        typeof item.normalized_message === 'string' &&
        typeof item.count === 'number' &&
        typeof item.last_seen === 'string',
    ),
  );
  check(
    '정렬은 count desc, last_seen desc',
    allGroups.items.every(
      (item, index) =>
        index === 0 ||
        allGroups.items[index - 1].count > item.count ||
        (allGroups.items[index - 1].count === item.count &&
          Date.parse(allGroups.items[index - 1].last_seen) >= Date.parse(item.last_seen)),
    ),
  );
  check(
    '심각도는 fingerprint 조인 그대로 (분석된 그룹은 값이 있다)',
    allGroups.items.some((item) => item.latest_severity != null) &&
      allGroups.items.some((item) => item.latest_severity == null),
  );
  const pagedGroups = await mockRequest<DashboardErrorGroupsResponse>(
    'GET',
    '/api/dashboard/error-groups',
    { query: { limit: 2, offset: 0 } },
  );
  check('limit 이 적용된다', pagedGroups.items.length === 2 && pagedGroups.total === allGroups.total);
  const secondPage = await mockRequest<DashboardErrorGroupsResponse>(
    'GET',
    '/api/dashboard/error-groups',
    { query: { limit: 2, offset: 2 } },
  );
  check(
    'offset 이 겹치지 않는 페이지를 준다',
    secondPage.items.every((item) => !pagedGroups.items.some((first) => first.id === item.id)),
  );

  console.log('스케줄 필드 (계약 — 0004)');
  const scheduled = await mockRequest<PolicyRead[]>('GET', '/api/policies', {});
  check(
    '정책에 스케줄 3 필드가 있다',
    scheduled.every(
      (policy) =>
        typeof policy.schedule_enabled === 'boolean' &&
        typeof policy.auto_analyze_new === 'boolean' &&
        (policy.schedule_interval_minutes === null ||
          typeof policy.schedule_interval_minutes === 'number'),
    ),
  );
  check(
    '스케줄 + 자동 분석이 켜진 정책이 있다',
    scheduled.some((policy) => policy.schedule_enabled && policy.auto_analyze_new),
  );
  const withSchedule = await mockRequest<PolicyRead>('POST', '/api/policies', {
    body: {
      loki_connection_id: 1,
      name: 'smoke-schedule',
      logql: '{service="payment-api"}',
      default_range_minutes: 30,
      max_lines: 100,
      exclusions: [],
      max_samples_per_group: 2,
      allow_ai_analysis: true,
      daily_analysis_limit: null,
      schedule_enabled: true,
      schedule_interval_minutes: 15,
      auto_analyze_new: true,
    },
  });
  check(
    '스케줄을 켜서 저장하면 그대로 남는다',
    withSchedule.schedule_enabled === true &&
      withSchedule.schedule_interval_minutes === 15 &&
      withSchedule.auto_analyze_new === true,
  );
  // 스케줄을 끄면 주기·자동 분석도 함께 내려가야 한다 — 다음에 켰을 때 예전 값으로
  // 조용히 돌기 시작하는 것을 막는 규칙이다.
  const scheduleOff = await mockRequest<PolicyRead>('PATCH', `/api/policies/${withSchedule.id}`, {
    body: { schedule_enabled: false },
  });
  check(
    '스케줄을 끄면 주기·자동 분석도 내려간다',
    scheduleOff.schedule_interval_minutes === null && scheduleOff.auto_analyze_new === false,
  );
  await mockRequest<void>('DELETE', `/api/policies/${withSchedule.id}`, {});

  console.log('triggered_by 배지 (수동/자동)');
  const historyRuns = await mockRequest<QueryRunListResponse>(
    'GET',
    '/api/policies/1/query-runs',
    {},
  );
  check(
    '실행 이력에 triggered_by 가 있다',
    historyRuns.items.every(
      (run) => run.triggered_by === 'manual' || run.triggered_by === 'schedule',
    ),
  );
  check(
    '수동·자동 실행이 모두 있다',
    historyRuns.items.some((run) => run.triggered_by === 'schedule') &&
      historyRuns.items.some((run) => run.triggered_by === 'manual'),
  );
  const manualRun = await mockRequest<QueryRunRead>('POST', '/api/policies/1/query-runs', {
    body: {},
  });
  check('화면에서 누른 실행은 manual', manualRun.triggered_by === 'manual');
  const jobList = await mockRequest<AnalysisJobListResponse>('GET', '/api/analysis-jobs', {});
  check(
    '분석 이력에도 triggered_by 가 실린다',
    jobList.items.some((job) => job.triggered_by === 'schedule') &&
      jobList.items.some((job) => job.triggered_by === 'manual'),
  );

  console.log('dashboard');
  const overview = await mockRequest<DashboardOverviewResponse>('GET', '/api/dashboard/overview', {
    query: { step_seconds: 300, top: 10 },
  });
  check('series 가 비어 있지 않다', overview.series.length > 0);
  check('total_errors > 0', overview.total_errors > 0);
  check('by_service 집계 존재', overview.by_service.length === 5);
  check(
    'analysis_status 가 fingerprint 로 채워진다',
    overview.top_groups.some((g) => g.analysis_status === 'succeeded') &&
      overview.top_groups.some((g) => g.analysis_status === null),
  );

  console.log('정책 상세 지표 (계약 — Phase 7 overview)');
  // top 을 일부러 작게 잡는다 — 지표가 `top_groups.length`(상위 N)를 쓰고 있으면 여기서 갈린다.
  const detail1 = await mockRequest<DashboardOverviewResponse>('GET', '/api/dashboard/overview', {
    query: { policy_id: 1, step_seconds: 300, top: 3 },
  });
  check(
    'group_count 는 회차 전체 COUNT (상위 N 이 아니다)',
    detail1.group_count === 6 && detail1.top_groups.length === 3,
    `group_count=${detail1.group_count} top=${detail1.top_groups.length}`,
  );
  check(
    'unanalyzed_group_count 도 회차 전체 기준',
    typeof detail1.unanalyzed_group_count === 'number' &&
      detail1.unanalyzed_group_count > 0 &&
      detail1.unanalyzed_group_count <= (detail1.group_count ?? 0),
  );
  // 시각별 합산이 계약이다 — 같은 timestamp 가 두 번 오면 차트가 한 시각을 여러 번 그린다.
  check(
    'series 는 시각별 합산 (timestamp 중복 없음)',
    new Set(detail1.series.map((point) => point.timestamp)).size === detail1.series.length,
  );
  check(
    '분모 쿼리가 있는 정책은 ingest_total 이 숫자',
    typeof detail1.ingest_total === 'number' && detail1.ingest_total > 0,
  );
  check(
    'ingest_series 합계가 ingest_total 과 일치 (같은 결과 재사용)',
    detail1.ingest_series.reduce((acc, point) => acc + point.value, 0) === detail1.ingest_total,
  );
  check(
    'error_ratio = total_errors / ingest_total',
    detail1.error_ratio !== null &&
      detail1.error_ratio != null &&
      Math.abs(detail1.error_ratio - detail1.total_errors / (detail1.ingest_total ?? 1)) < 1e-9,
  );
  check(
    'error_ratio 는 0 초과 1 이하 (분모가 오류보다 크다)',
    (detail1.error_ratio ?? 0) > 0 && (detail1.error_ratio ?? 0) <= 1,
  );

  // 분모 쿼리가 없는 정책 — **0 이 아니라 null** 이어야 화면이 `-` 로 그린다.
  const detail2 = await mockRequest<DashboardOverviewResponse>('GET', '/api/dashboard/overview', {
    query: { policy_id: 2, step_seconds: 300, top: 10 },
  });
  check('분모 쿼리 미설정이면 ingest_total 은 null (0 이 아니다)', detail2.ingest_total === null);
  check('미설정이면 ingest_series 는 빈 배열', detail2.ingest_series.length === 0);
  check('미설정이면 error_ratio 도 null', detail2.error_ratio === null);

  console.log('policies');
  const created = await mockRequest<PolicyRead>('POST', '/api/policies', {
    body: {
      loki_connection_id: 1,
      name: 'smoke',
      logql: '{service="payment-api"}',
      default_range_minutes: 30,
      max_lines: 100,
      exclusions: [],
      max_samples_per_group: 2,
      allow_ai_analysis: true,
      daily_analysis_limit: null,
    },
  });
  check('정책 생성', created.id > 0 && created.active);
  const list = await mockRequest<PolicyRead[]>('GET', '/api/policies', {});
  check('생성한 정책이 목록에 있다', list.some((p) => p.id === created.id));
  await mockRequest<void>('DELETE', `/api/policies/${created.id}`, {});
  const after = await mockRequest<PolicyRead[]>('GET', '/api/policies', {});
  check(
    'DELETE 는 삭제가 아니라 비활성화',
    after.some((p) => p.id === created.id && !p.active),
  );

  const preview = await mockRequest<PolicyPreviewResponse>('POST', '/api/policies/preview', {
    body: {
      loki_connection_id: 1,
      logql: '{service="payment-api"} | json | level="ERROR"',
      range_minutes: 60,
      limit: 20,
      exclusions: [],
    },
  });
  check('미리보기 sample_lines 존재', preview.sample_lines.length > 0);
  check(
    '미리보기 라인이 마스킹된 형태',
    preview.sample_lines.every((line) => !/sk-[A-Za-z0-9]{8}/.test(line)),
  );
  check(
    '| json 파싱 실패 경고를 올린다',
    preview.warnings.some((w) => w.code === 'parse_error'),
  );

  // 계약 2 — 정책 실행 이력. 봉투 모양과 최신순 정렬이 라이브와 같아야 한다.
  const runs = await mockRequest<QueryRunListResponse>('GET', '/api/policies/1/query-runs', {
    query: { limit: 20, offset: 0 },
  });
  check('실행 이력 봉투 {total, limit, offset, items}', typeof runs.total === 'number' && Array.isArray(runs.items));
  check('실행 이력이 최신순', runs.items.every((run, i) => i === 0 || Date.parse(runs.items[i - 1].started_at) >= Date.parse(run.started_at)));
  check('이력에 정책 id 가 일치', runs.items.every((run) => run.policy_id === 1));
  check(
    '이력에 상태·건수·그룹 수·경고가 있다',
    runs.items.every(
      (run) =>
        typeof run.status === 'string' &&
        typeof run.fetched_count === 'number' &&
        typeof run.dropped_count === 'number' &&
        typeof run.group_count === 'number' &&
        Array.isArray(run.warnings),
    ),
  );
  check(
    '없는 정책의 이력은 404',
    await expectStatus(() => mockRequest('GET', '/api/policies/999999/query-runs', {}), 404),
  );

  console.log('분모 쿼리 (계약 — baseline_query)');
  const seeded = await mockRequest<PolicyRead[]>('GET', '/api/policies', {});
  check(
    '분모 쿼리를 가진 정책과 없는 정책이 둘 다 있다',
    seeded.some((policy) => typeof policy.baseline_query === 'string' && policy.baseline_query) &&
      seeded.some((policy) => !policy.baseline_query),
  );
  const withBaseline = await mockRequest<PolicyRead>('POST', '/api/policies', {
    body: {
      loki_connection_id: 1,
      name: 'smoke-baseline',
      logql: '{service="payment-api"} | json | level="ERROR"',
      baseline_query: '{service="payment-api"}',
      default_range_minutes: 30,
      max_lines: 100,
      exclusions: [],
      max_samples_per_group: 2,
      allow_ai_analysis: true,
      daily_analysis_limit: null,
    },
  });
  check('저장한 분모 쿼리가 그대로 남는다', withBaseline.baseline_query === '{service="payment-api"}');
  // 빈 입력은 화면이 **null 로** 보낸다 — 빈 문자열이 저장되면 "설정했는데 못 세는" 상태가 된다.
  const clearedBaseline = await mockRequest<PolicyRead>(
    'PATCH',
    `/api/policies/${withBaseline.id}`,
    { body: { baseline_query: null } },
  );
  check('명시적 null 로 분모 쿼리를 지운다', clearedBaseline.baseline_query === null);
  const emptyBaseline = await mockRequest<PolicyRead>('PATCH', `/api/policies/${withBaseline.id}`, {
    body: { baseline_query: '   ' },
  });
  check('빈 문자열도 미설정으로 접힌다', emptyBaseline.baseline_query === null);
  await mockRequest<void>('DELETE', `/api/policies/${withBaseline.id}`, {});

  console.log('수집 확인 대상 (계약 — expected_services)');
  const lokiConns = await mockRequest<LokiConnectionRead[]>('GET', '/api/loki-connections', {});
  check(
    '연결에 expected_services 가 실린다',
    lokiConns.every((connection) => Array.isArray(connection.expected_services)),
  );
  check(
    '확인 대상이 있는 연결과 없는 연결이 둘 다 있다',
    lokiConns.some((connection) => (connection.expected_services ?? []).length > 0) &&
      lokiConns.some((connection) => (connection.expected_services ?? []).length === 0),
  );
  const newConn = await mockRequest<LokiConnectionRead>('POST', '/api/loki-connections', {
    body: {
      name: 'smoke-loki',
      source_type: 'loki',
      base_url: 'http://loki:3100',
      auth_type: 'none',
      label_mapping: { app: 'service' },
      active: true,
      expected_services: ['payment-api', 'billing-api'],
    },
  });
  check(
    '저장한 확인 대상이 그대로 남는다',
    newConn.expected_services.join(',') === 'payment-api,billing-api',
  );
  check('secret 은 응답에 평문으로 오지 않는다', !JSON.stringify(newConn).includes('secret":"'));
  // 빈 배열은 "확인을 끈다"는 명시적 값이다 — 생략(변경 없음)과 구분되어야 한다.
  const offConn = await mockRequest<LokiConnectionRead>(
    'PATCH',
    `/api/loki-connections/${newConn.id}`,
    { body: { expected_services: [] } },
  );
  check('빈 배열로 수집 확인을 끌 수 있다', offConn.expected_services.length === 0);
  const untouched = await mockRequest<LokiConnectionRead>(
    'PATCH',
    `/api/loki-connections/${newConn.id}`,
    { body: { name: 'smoke-loki-2' } },
  );
  check('생략은 변경 없음이다', untouched.expected_services.length === 0);
  await mockRequest<void>('DELETE', `/api/loki-connections/${newConn.id}`, {});

  console.log('수집 중단 경고 (계약 — ingest_absent)');
  const badgeRuns = await mockRequest<QueryRunListResponse>('GET', '/api/policies/1/query-runs', {
    query: { limit: 20, offset: 0 },
  });
  const absentWarnings = badgeRuns.items
    .flatMap((run) => run.warnings)
    .filter((warning) => warning.code === 'ingest_absent');
  check('조회 회차 경고에 ingest_absent 가 남는다', absentWarnings.length > 0);
  check(
    '메시지에 부재 서비스 이름이 그대로 실린다 (색 없이도 읽힌다)',
    absentWarnings.some((warning) => warning.message.includes('billing-api')),
  );
  check(
    'count 는 부재 서비스 수',
    absentWarnings.every((warning) => typeof warning.count === 'number' && warning.count >= 1),
  );
  // 홈 카드의 배지는 `last_run.warnings` 로 그린다 — 여기 없으면 카드에서 사라진다.
  const badgeSummary = await mockRequest<DashboardSummaryResponse>(
    'GET',
    '/api/dashboard/summary',
    {},
  );
  check(
    'summary 의 last_run.warnings 에도 실린다 (홈 카드 배지의 출처)',
    badgeSummary.policies.some((policy) =>
      (policy.last_run?.warnings ?? []).some((warning) => warning.code === 'ingest_absent'),
    ),
  );

  console.log('llm 모델 목록 (계약 1)');
  // 조회지만 POST 다 — api_key 를 쿼리스트링에 실으면 평문 키가 액세스 로그에 남는다.
  const anthropicModels = await mockRequest<LLMModelListResponse>(
    'POST',
    '/api/llm-connections/models',
    { body: { provider: 'anthropic', connection_id: 1 } },
  );
  check('저장된 연결로 모델 목록 조회', anthropicModels.models.length > 0);
  check('응답 모양 {provider, models}', anthropicModels.provider === 'anthropic');
  const compatModels = await mockRequest<LLMModelListResponse>(
    'POST',
    '/api/llm-connections/models',
    { body: { provider: 'openai_compatible', base_url: 'http://llm-mock:8000/v1' } },
  );
  check('openai_compatible 은 base URL 로 조회', compatModels.models.length > 0);
  check(
    '키도 base URL 도 없으면 400 (화면은 자유 입력으로 폴백)',
    await expectStatus(
      () => mockRequest('POST', '/api/llm-connections/models', { body: { provider: 'openai' } }),
      400,
    ),
  );
  check(
    '모델 목록에 GET 은 없다 (쿼리스트링 키 유출 차단)',
    await expectStatus(
      () =>
        mockRequest('GET', '/api/llm-connections/models', { query: { provider: 'openai' } }),
      404,
    ),
  );

  console.log('settings · 모델 단가표');
  const pricing = await mockRequest<AppSettingRead>('GET', '/api/settings/model_pricing', {});
  check('단가표에 claude 가 있다', 'claude-sonnet-4-6' in asModelPricingTable(pricing.value));
  check('단가표에 gpt-5.2 는 없다 (추정 비용 null 경로)', !('gpt-5.2' in asModelPricingTable(pricing.value)));
  const merged = {
    ...asModelPricingTable(pricing.value),
    'gpt-5.2': { input_per_1k: 0.00125, output_per_1k: 0.01, currency: 'USD' },
  };
  const saved = await mockRequest<AppSettingRead>('PUT', '/api/settings/model_pricing', {
    body: { value: merged },
  });
  const savedTable = asModelPricingTable(saved.value);
  check('단가 등록 후 두 모델이 모두 남는다 (병합)', 'gpt-5.2' in savedTable && 'claude-sonnet-4-6' in savedTable);
  check(
    '형식이 깨진 단가표는 422 로 막는다',
    await expectStatus(
      () => mockRequest('PUT', '/api/settings/model_pricing', { body: { value: { 'x': { input_per_1k: -1 } } } }),
      422,
    ),
  );
  check(
    '화이트리스트 밖 키는 404',
    await expectStatus(() => mockRequest('GET', '/api/settings/nope', {}), 404),
  );
  // 원래대로 돌려놓는다 — 아래 usage 단언이 "단가 없음" 상태를 본다.
  await mockRequest<AppSettingRead>('PUT', '/api/settings/model_pricing', {
    body: { value: asModelPricingTable(pricing.value) },
  });

  console.log('error groups');
  const groups = await mockRequest<ErrorGroupListResponse>(
    'GET',
    '/api/query-runs/5001/error-groups',
    { query: { limit: 50, offset: 0 } },
  );
  check('그룹 6 개', groups.total === 6);
  check('발생 수 내림차순', groups.items.every((g, i) => i === 0 || groups.items[i - 1].count >= g.count));

  // 103 = 아직 분석되지 않은 그룹
  const detail = await mockRequest<ErrorGroupDetail>('GET', '/api/error-groups/103', {});
  check('미분석 그룹의 analysis_status 는 null', detail.analysis_status === null);
  check('대표 로그 존재', detail.samples.length > 0);
  check('추이 존재', detail.trend.length > 0);
  // 플레이스홀더 표식은 백엔드가 실제로 넣는 `<MASKED:종류>` 여야 한다.
  // 표식이 다르면 화면·복사·보고서에서 "마스킹된 자리"가 mock 과 실제에서 갈린다.
  const allMasked = groups.items
    .map((g) => g.normalized_message)
    .concat(detail.samples.map((s) => s.masked_log))
    .join('\n');
  check('마스킹 표식이 <MASKED:종류> 형식', /<MASKED:[A-Z_]+>/.test(allMasked), allMasked.slice(0, 120));
  check('구식 [REDACTED:*] 표식이 없다', !allMasked.includes('[REDACTED'));

  console.log('analysis job 상태 전이');
  const job = await mockRequest<AnalysisJobCreateResponse>(
    'POST',
    '/api/error-groups/103/analysis-jobs',
    { body: {} },
  );
  check('생성 직후 pending, reused=false', job.status === 'pending' && !job.reused, job.status);
  check('기본 연결을 골랐다', job.provider === 'anthropic');

  const again = await mockRequest<AnalysisJobCreateResponse>(
    'POST',
    '/api/error-groups/103/analysis-jobs',
    { body: {} },
  );
  check('진행 중이면 멱등하게 기존 작업 반환', again.reused && again.id === job.id);

  await sleep(2200);
  const running = await mockRequest<AnalysisJobRead>('GET', `/api/analysis-jobs/${job.id}`, {});
  check('2 초 뒤 running', running.status === 'running', running.status);

  await sleep(4500);
  const done = await mockRequest<AnalysisJobRead>('GET', `/api/analysis-jobs/${job.id}`, {});
  check('7 초 뒤 succeeded', done.status === 'succeeded', done.status);
  check('결과에 가설 1 개 이상', (done.result?.hypotheses.length ?? 0) >= 1);
  check('결과에 한계 1 개 이상', (done.result?.limitations.length ?? 0) >= 1);
  check('사용량 기록 존재', done.usage != null && done.usage.input_tokens > 0);

  const rejoined = await mockRequest<ErrorGroupDetail>('GET', '/api/error-groups/103', {});
  check('그룹 상태가 succeeded 로 갱신', rejoined.analysis_status === 'succeeded');
  check('fingerprint 이력에 조인', rejoined.analyses.some((a) => a.id === job.id));

  console.log('report');
  const report = await mockRequest<string>('GET', `/api/analysis-jobs/${job.id}/report`, {
    parse: 'text',
  });
  check('보고서가 문자열', typeof report === 'string' && report.length > 500);
  check('"LLM 이 생성한 원인 가설" 표기 포함', report.includes('LLM 이 생성한 원인 가설'));
  check('원본 로그 복귀 경로 포함', report.includes('원본 로그로 돌아가기'));
  check('"추정" 표기 포함', report.includes('추정'));

  console.log('usage');
  const usage = await mockRequest<UsageResponse>('GET', '/api/usage', {});
  check('모델별 집계 존재', usage.items.length >= 2);
  check('실패 건수 집계', usage.items.some((i) => i.failure_count > 0));
  check('총 추정 비용 > 0', Number(usage.total_estimated_cost) > 0);
  // 단가표에 없는 모델의 비용은 0 이 아니라 null 이다 (화면은 `-` + "단가 등록" 버튼).
  check(
    '단가 미등록 모델의 추정 비용은 null',
    usage.items.some((item) => item.model === 'gpt-5.2' && item.estimated_cost === null),
  );
  check(
    '단가 등록 모델의 추정 비용은 값이 있다',
    usage.items.some((item) => item.model === 'claude-sonnet-4-6' && item.estimated_cost !== null),
  );
  // 분해를 요청하지 않으면 **null** 이다 — 빈 배열이면 "분해했더니 비었다"와 구분되지 않고,
  // 화면이 안내 문구 대신 "기록 없음"을 보여주게 된다. 라이브 백엔드도 null 을 싣는다.
  check('group_by 없이 부르면 buckets 는 null (빈 배열이 아니다)', usage.buckets === null);

  console.log('일일 한도 게이지 (계약 — GET /usage/daily-limit)');
  const dailyLimit = await mockRequest<DailyLimitResponse>('GET', '/api/usage/daily-limit', {});
  check(
    '봉투 {date, timezone, global_limit, global_used, policies}',
    typeof dailyLimit.date === 'string' &&
      typeof dailyLimit.timezone === 'string' &&
      typeof dailyLimit.global_limit === 'number' &&
      typeof dailyLimit.global_used === 'number' &&
      Array.isArray(dailyLimit.policies),
  );
  check('date 는 로컬 날짜 YYYY-MM-DD', /^\d{4}-\d{2}-\d{2}$/.test(dailyLimit.date));
  // 하루 경계는 사용량 분해(group_by=day)와 **같은 계산**이어야 두 화면의 "오늘"이 같다.
  check('timezone 은 app_settings 값 (서버 로케일이 아니다)', dailyLimit.timezone === 'Asia/Seoul');
  check(
    '오늘 사용량은 0 이상이고 전체 실행 수를 넘지 않는다',
    dailyLimit.global_used >= 0 && dailyLimit.global_used <= usage.total_jobs + 5,
  );
  check(
    '정책 행은 {policy_id, name, limit, used}',
    dailyLimit.policies.every(
      (policy) =>
        typeof policy.policy_id === 'number' &&
        typeof policy.name === 'string' &&
        typeof policy.limit === 'number' &&
        typeof policy.used === 'number',
    ),
  );
  check('자체 한도를 가진 정책이 실린다', dailyLimit.policies.some((policy) => policy.policy_id === 1));
  // 한도가 없는 정책을 실으면 "정책마다 별도 상한이 있다"로 읽힌다 (계약).
  check(
    '자체 한도가 없는 정책은 실리지 않는다',
    !dailyLimit.policies.some((policy) => policy.policy_id === 2),
  );

  console.log('사용량 분해 (계약 3 — group_by)');
  const byDay = await mockRequest<UsageResponse>('GET', '/api/usage', {
    query: { group_by: 'day' },
  });
  check('day 분해에 buckets 가 실린다', Array.isArray(byDay.buckets) && byDay.buckets.length > 0);
  check(
    '기존 봉투는 그대로 (items·합계가 남는다)',
    Array.isArray(byDay.items) && typeof byDay.total_jobs === 'number',
  );
  check(
    'day 버킷 모양 {key, label, input_tokens, output_tokens, estimated_cost, job_count, failure_count}',
    (byDay.buckets ?? []).every(
      (bucket) =>
        typeof bucket.key === 'string' &&
        typeof bucket.label === 'string' &&
        typeof bucket.input_tokens === 'number' &&
        typeof bucket.output_tokens === 'number' &&
        typeof bucket.job_count === 'number' &&
        typeof bucket.failure_count === 'number' &&
        (bucket.estimated_cost === null ||
          typeof bucket.estimated_cost === 'string' ||
          typeof bucket.estimated_cost === 'number'),
    ),
  );
  check(
    'day 의 key 는 YYYY-MM-DD 이고 label 과 같다',
    (byDay.buckets ?? []).every(
      (bucket) => /^\d{4}-\d{2}-\d{2}$/.test(bucket.key) && bucket.label === bucket.key,
    ),
  );
  check(
    'day 버킷의 실행 수 합계가 전체와 같다 (버리는 작업이 없다)',
    (byDay.buckets ?? []).reduce((acc, bucket) => acc + bucket.job_count, 0) === byDay.total_jobs,
  );

  const byPolicy = await mockRequest<UsageResponse>('GET', '/api/usage', {
    query: { group_by: 'policy' },
  });
  check(
    'policy 분해에 buckets 가 실린다',
    Array.isArray(byPolicy.buckets) && byPolicy.buckets.length > 0,
  );
  check(
    'policy 의 key 는 policy_id 문자열 또는 "unknown"',
    (byPolicy.buckets ?? []).every(
      (bucket) => bucket.key === 'unknown' || /^\d+$/.test(bucket.key),
    ),
  );
  // 정책 연결이 끊긴 작업을 버리면 합계가 어긋난다 — unknown 버킷이 그 자리를 받는다.
  check(
    '정책 연결이 끊긴 작업은 unknown 버킷에 남는다',
    (byPolicy.buckets ?? []).some((bucket) => bucket.key === 'unknown'),
  );
  check(
    'policy 버킷의 실행 수 합계가 전체와 같다',
    (byPolicy.buckets ?? []).reduce((acc, bucket) => acc + bucket.job_count, 0) ===
      byPolicy.total_jobs,
  );
  // 단가 미등록 모델만 있는 칸의 비용은 0 이 아니라 null 이다 (막대를 그리지 않는다).
  check(
    '비용을 계산할 수 없는 버킷은 null (0 이 아니다)',
    (byPolicy.buckets ?? []).some((bucket) => bucket.estimated_cost === null),
  );

  console.log('분석 이력 검색 (계약 2 — q · 기간 · 페이지네이션)');
  const jobsAll = await mockRequest<AnalysisJobListResponse>('GET', '/api/analysis-jobs', {
    query: { limit: 100, offset: 0 },
  });
  check(
    '봉투 {total, limit, offset, items} 유지',
    typeof jobsAll.total === 'number' &&
      typeof jobsAll.limit === 'number' &&
      typeof jobsAll.offset === 'number' &&
      Array.isArray(jobsAll.items),
  );
  check(
    '최신순',
    jobsAll.items.every(
      (job, index) =>
        index === 0 ||
        Date.parse(jobsAll.items[index - 1].requested_at) >= Date.parse(job.requested_at),
    ),
  );

  const byService = await mockRequest<AnalysisJobListResponse>('GET', '/api/analysis-jobs', {
    query: { q: 'payment-api' },
  });
  check(
    'q 는 서비스에 부분 일치한다',
    byService.items.length > 0 && byService.items.every((job) => job.service === 'payment-api'),
  );
  check('q 는 total 도 좁힌다', byService.total < jobsAll.total && byService.total > 0);
  const byFingerprint = await mockRequest<AnalysisJobListResponse>('GET', '/api/analysis-jobs', {
    query: { q: 'fp_3b77' },
  });
  check(
    'q 는 fingerprint 에도 부분 일치한다',
    byFingerprint.items.length > 0 &&
      byFingerprint.items.every((job) => job.fingerprint.includes('fp_3b77')),
  );
  const byModel = await mockRequest<AnalysisJobListResponse>('GET', '/api/analysis-jobs', {
    query: { q: 'gpt-5.2' },
  });
  check(
    'q 는 모델에도 부분 일치한다',
    byModel.items.length > 0 && byModel.items.every((job) => job.model.includes('gpt-5.2')),
  );
  const noHit = await mockRequest<AnalysisJobListResponse>('GET', '/api/analysis-jobs', {
    query: { q: 'zzz-no-such-thing' },
  });
  check('일치가 없으면 빈 목록 + total 0', noHit.items.length === 0 && noHit.total === 0);

  // 기간 필터. `requested_from` 하한만 걸면 그 시각 이후만 남아야 한다.
  const since = new Date(Date.now() - 24 * 60 * 60_000).toISOString();
  const recent = await mockRequest<AnalysisJobListResponse>('GET', '/api/analysis-jobs', {
    query: { requested_from: since },
  });
  check(
    'requested_from 하한이 적용된다',
    recent.items.every((job) => Date.parse(job.requested_at) >= Date.parse(since)),
  );
  check('기간 필터가 total 을 좁힌다', recent.total < jobsAll.total);
  const oldOnly = await mockRequest<AnalysisJobListResponse>('GET', '/api/analysis-jobs', {
    query: { requested_to: since },
  });
  check(
    'requested_to 상한이 적용된다',
    oldOnly.items.length > 0 &&
      oldOnly.items.every((job) => Date.parse(job.requested_at) <= Date.parse(since)),
  );
  check('from + to 합이 전체와 같다', recent.total + oldOnly.total === jobsAll.total);

  const statusFiltered = await mockRequest<AnalysisJobListResponse>('GET', '/api/analysis-jobs', {
    query: { status: 'failed' },
  });
  check(
    '상태 필터는 그대로 동작한다 (기존 파라미터)',
    statusFiltered.items.length > 0 &&
      statusFiltered.items.every((job) => job.status === 'failed'),
  );

  // 페이지네이션 — total 은 **필터 적용 뒤** 건수여야 페이지 수가 맞는다.
  const page1 = await mockRequest<AnalysisJobListResponse>('GET', '/api/analysis-jobs', {
    query: { limit: 2, offset: 0 },
  });
  const page2 = await mockRequest<AnalysisJobListResponse>('GET', '/api/analysis-jobs', {
    query: { limit: 2, offset: 2 },
  });
  check('limit·offset 이 적용된다', page1.items.length === 2 && page2.items.length > 0);
  check(
    '페이지가 겹치지 않는다',
    page2.items.every((job) => !page1.items.some((first) => first.id === job.id)),
  );
  check('페이지를 넘겨도 total 은 같다', page1.total === jobsAll.total && page2.total === jobsAll.total);

  console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILED`);
  process.exit(failures === 0 ? 0 : 1);
}

void main();
