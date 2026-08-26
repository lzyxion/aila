/**
 * mock 계층 스모크 테스트 (개발용, 앱 번들에 포함되지 않는다).
 *
 *   npx vite build --ssr scripts/mock-smoke.ts --outDir <out>
 *   node <out>/mock-smoke.js
 *
 * 확인 대상: 라우팅, 분석 작업의 pending -> running -> succeeded 전이,
 * 멱등 재사용, fingerprint 기준 분석 상태, 보고서 렌더링.
 */

import { mockRequest } from '../src/api/mock/handler';
import type {
  AnalysisJobCreateResponse,
  AnalysisJobRead,
  DashboardOverviewResponse,
  ErrorGroupDetail,
  ErrorGroupListResponse,
  PolicyPreviewResponse,
  PolicyRead,
  UsageResponse,
} from '../src/api/types';

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

  console.log(failures === 0 ? '\nALL PASS' : `\n${failures} FAILED`);
  process.exit(failures === 0 ? 0 : 1);
}

void main();
