/**
 * mock 계층 스모크 테스트 (개발용, 앱 번들에 포함되지 않는다).
 *
 *   npx vite build --ssr scripts/mock-smoke.ts --outDir <out>
 *   node <out>/mock-smoke.js
 *
 * 확인 대상: 라우팅, 분석 작업의 pending -> running -> succeeded 전이,
 * 멱등 재사용, fingerprint 기준 분석 상태, 보고서 렌더링.
 */

import { ApiError } from '../src/api/client';
import { mockRequest } from '../src/api/mock/handler';
import type {
  AnalysisJobCreateResponse,
  AnalysisJobRead,
  AppSettingRead,
  DashboardOverviewResponse,
  ErrorGroupDetail,
  ErrorGroupListResponse,
  LLMModelListResponse,
  PolicyPreviewResponse,
  PolicyRead,
  QueryRunListResponse,
  UsageResponse,
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

async function main(): Promise<void> {
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

  console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILED`);
  process.exit(failures === 0 ? 0 : 1);
}

void main();
