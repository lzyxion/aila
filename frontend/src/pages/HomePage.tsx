/**
 * 홈 — 통합 대시보드 (정책 카드 그리드).
 *
 * 이 화면이 답해야 하는 질문은 하나다: **"지금 무엇을 봐야 하는가."** 정책이 스무 개가
 * 되어도 훑을 수 있어야 하므로 기본 정렬은 이름순이 아니라 **미분석 신규 그룹이 많은 순**
 * 이다 — "새로 나타났는데 아무도 보지 않은 오류"가 위로 온다.
 *
 * 정책 하나의 상세(추이·서비스별·상위 그룹)는 여기 있지 않다. 카드에서 `/dashboard/:policyId`
 * 로 들어간다.
 *
 * 표시 규칙(계약):
 * - `total_errors_24h` 는 `count_over_time` metric 기준이고 로그 라인 수가 아니다.
 *   metric 쿼리에 실패하면 **null** 이며 0 이 아니다 — 화면은 `-` 로 쓴다.
 * - `unanalyzed_group_count` 는 그룹 id 가 아니라 **fingerprint 기준**이다.
 * - 실행은 쓰기 동작이라 `admin` 만 누를 수 있다. 판정은 서버가 한다.
 */

import { useMemo, useState } from 'react';
import { Link } from 'react-router';

import { isEndpointMissing } from '../api/client';
import { useDashboardSummary, usePolicies, useRunPolicy } from '../api/queries';
import type { DashboardSummaryPolicy, PolicyRead } from '../api/types';
import { policySchedule } from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import { QueryRunStatusBadge, ScheduleBadge } from '../components/StatusBadges';
import {
  Badge,
  Button,
  Card,
  EmptyBlock,
  ErrorBlock,
  Field,
  Input,
  LoadingBlock,
  Notice,
  PageHeader,
  PlayIcon,
  Select,
  Spinner,
  cx,
} from '../components/ui';
import {
  formatDateTime,
  formatNumber,
  formatRelative,
  warningCodeLabel,
} from '../lib/format';

type SortKey = 'unanalyzed' | 'errors' | 'recent' | 'name';

const SORTS: Array<{ key: SortKey; label: string }> = [
  { key: 'unanalyzed', label: '미분석 신규 그룹 많은 순' },
  { key: 'errors', label: '24h 오류 많은 순' },
  { key: 'recent', label: '최근 실행순' },
  { key: 'name', label: '정책명순' },
];

export function HomePage() {
  const summaryQuery = useDashboardSummary();
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortKey>('unanalyzed');

  const cards = useMemo(
    () => sortCards(filterCards(summaryQuery.data?.policies ?? [], search), sort),
    [search, sort, summaryQuery.data],
  );

  const totalUnanalyzed = (summaryQuery.data?.policies ?? []).reduce(
    (acc, policy) => acc + policy.unanalyzed_group_count,
    0,
  );

  return (
    <div>
      <PageHeader
        title="통합 대시보드"
        description={
          <>
            정책별 최근 상태 요약입니다. <strong>미분석 신규 그룹</strong>은 최근 성공한 조회의
            그룹 중 <strong>fingerprint 기준</strong>으로 분석 이력이 전혀 없는 수이고, 24시간
            오류 건수는 <code className="rounded bg-slate-200 px-1">count_over_time</code> metric
            기준이라 로그 라인 수가 아닙니다.
          </>
        }
        actions={
          summaryQuery.data && (
            <span className="text-xs text-slate-500">
              생성 {formatDateTime(summaryQuery.data.generated_at)}
            </span>
          )
        }
      />

      {summaryQuery.isPending && <LoadingBlock />}

      {/* 백엔드에 아직 이 경로가 없으면 실패로 표시하지 않고 축소 카드로 물러난다. */}
      {summaryQuery.isError &&
        (isEndpointMissing(summaryQuery.error) ? (
          <SummaryFallback />
        ) : (
          <ErrorBlock error={summaryQuery.error} />
        ))}

      {summaryQuery.data && (
        <>
          <Card className="mb-6">
            <div className="flex flex-wrap items-end gap-4">
              <Field label="정책 검색" className="min-w-56 flex-1">
                <Input
                  value={search}
                  placeholder="정책명으로 좁히기"
                  onChange={(event) => setSearch(event.target.value)}
                />
              </Field>
              <Field label="정렬" className="w-64">
                <Select value={sort} onChange={(event) => setSort(event.target.value as SortKey)}>
                  {SORTS.map((item) => (
                    <option key={item.key} value={item.key}>
                      {item.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <p className="mb-2 text-xs text-slate-500">
                정책 {formatNumber(summaryQuery.data.policies.length)}개 · 미분석 신규 그룹 합계{' '}
                <strong className="text-slate-800">{formatNumber(totalUnanalyzed)}</strong>
              </p>
            </div>
          </Card>

          {summaryQuery.data.policies.length === 0 ? (
            <EmptyBlock>
              저장된 정책이 없습니다. <Link to="/policies" className="text-sky-800 underline">분석 정책</Link>{' '}
              화면에서 하나를 만드십시오.
            </EmptyBlock>
          ) : cards.length === 0 ? (
            <EmptyBlock>&quot;{search}&quot; 에 해당하는 정책이 없습니다.</EmptyBlock>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
              {cards.map((policy) => (
                <PolicyCard key={policy.policy_id} policy={policy} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function filterCards(
  policies: DashboardSummaryPolicy[],
  search: string,
): DashboardSummaryPolicy[] {
  const needle = search.trim().toLowerCase();
  if (!needle) return policies;
  return policies.filter((policy) => policy.name.toLowerCase().includes(needle));
}

function sortCards(
  policies: DashboardSummaryPolicy[],
  sort: SortKey,
): DashboardSummaryPolicy[] {
  const rows = [...policies];
  switch (sort) {
    case 'unanalyzed':
      // 동점이면 24h 오류가 많은 쪽이 위 — 둘 다 0 인 정책끼리는 이름으로 고정한다
      // (정렬이 흔들리면 카드가 새로고침마다 자리를 바꾼다).
      return rows.sort(
        (a, b) =>
          b.unanalyzed_group_count - a.unanalyzed_group_count ||
          (b.total_errors_24h ?? -1) - (a.total_errors_24h ?? -1) ||
          a.name.localeCompare(b.name),
      );
    case 'errors':
      // metric 실패(null)는 0 과 다르다 — 맨 아래로 보내되 "없음"으로 취급하지 않는다.
      return rows.sort(
        (a, b) =>
          (b.total_errors_24h ?? -1) - (a.total_errors_24h ?? -1) || a.name.localeCompare(b.name),
      );
    case 'recent':
      return rows.sort(
        (a, b) =>
          Date.parse(b.last_run?.started_at ?? '') - Date.parse(a.last_run?.started_at ?? '') ||
          a.name.localeCompare(b.name),
      );
    default:
      return rows.sort((a, b) => a.name.localeCompare(b.name));
  }
}

// ------------------------------------------------------------------- 카드

function PolicyCard({ policy }: { policy: DashboardSummaryPolicy }) {
  const write = useWriteAccess();
  const runPolicy = useRunPolicy();
  const lastRun = policy.last_run;
  const unanalyzed = policy.unanalyzed_group_count;

  return (
    <section
      className={cx(
        'flex flex-col rounded-xl border bg-white shadow-sm shadow-slate-200/50',
        unanalyzed > 0 ? 'border-amber-300' : 'border-slate-200',
        !policy.active && 'opacity-75',
      )}
    >
      <header className="border-b border-slate-100 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <h2 className="min-w-0 text-base font-semibold text-slate-900">
            <Link to={`/dashboard/${policy.policy_id}`} className="hover:underline">
              {policy.name}
            </Link>
          </h2>
          <span className="text-xs text-slate-400">#{policy.policy_id}</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Badge tone={policy.active ? 'success' : 'neutral'}>
            {policy.active ? '활성' : '비활성'}
          </Badge>
          <ScheduleBadge
            enabled={policy.schedule_enabled}
            intervalMinutes={policy.schedule_interval_minutes}
          />
        </div>
      </header>

      <div className="grid grid-cols-2 gap-px bg-slate-100">
        {/* 이 화면에서 가장 중요한 숫자 — 카드에서 가장 크게 둔다. */}
        <div className="bg-white px-5 py-3">
          <p className="text-xs font-medium text-slate-500">미분석 신규 그룹</p>
          <p
            className={cx(
              'mt-1 text-3xl font-bold tabular-nums',
              unanalyzed > 0 ? 'text-amber-700' : 'text-slate-400',
            )}
            title="최근 성공한 조회의 그룹 중 fingerprint 기준으로 분석 이력이 전혀 없는 수입니다."
          >
            {formatNumber(unanalyzed)}
          </p>
        </div>
        <div className="bg-white px-5 py-3">
          <p className="text-xs font-medium text-slate-500">24h 오류 건수</p>
          <p
            className="mt-1 text-3xl font-bold text-slate-900 tabular-nums"
            title={
              policy.total_errors_24h === null
                ? 'metric 쿼리에 실패해 계산하지 못했습니다. 0 건이라는 뜻이 아닙니다.'
                : 'count_over_time metric 기준 — 로그 라인 수가 아닙니다.'
            }
          >
            {policy.total_errors_24h === null ? (
              <span className="text-slate-400">-</span>
            ) : (
              formatNumber(policy.total_errors_24h)
            )}
          </p>
          {policy.total_errors_24h === null && (
            <p className="mt-0.5 text-xs text-amber-700">metric 쿼리 실패</p>
          )}
        </div>
      </div>

      <div className="flex-1 border-t border-slate-100 px-5 py-4">
        <p className="text-xs font-medium text-slate-500">최근 실행</p>
        {lastRun ? (
          <div className="mt-1.5 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <QueryRunStatusBadge status={lastRun.status} />
              <span className="text-sm text-slate-700" title={formatDateTime(lastRun.started_at)}>
                {formatRelative(lastRun.started_at)}
              </span>
              <span className="text-xs text-slate-400">#{lastRun.id}</span>
            </div>
            <p className="text-xs text-slate-600">
              {formatNumber(lastRun.fetched_count)} 라인 조회 ·{' '}
              <strong className="text-slate-800">{formatNumber(lastRun.group_count)}</strong> 개 그룹
            </p>
            {lastRun.warnings.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {lastRun.warnings.map((warning, index) => (
                  <Badge key={`${warning.code}-${index}`} tone="warning" title={warning.message}>
                    {warningCodeLabel(warning.code)}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        ) : (
          <p className="mt-1.5 text-sm text-slate-500">아직 실행 이력이 없습니다.</p>
        )}

        {policy.warnings.length > 0 && (
          <ul className="mt-3 space-y-1 text-xs text-amber-800">
            {policy.warnings.map((warning, index) => (
              <li key={`${warning.code}-${index}`}>
                <strong>{warningCodeLabel(warning.code)}</strong> — {warning.message}
              </li>
            ))}
          </ul>
        )}

        {runPolicy.isSuccess && runPolicy.variables?.id === policy.policy_id && (
          <div className="mt-3">
            <Notice tone="success" title={`조회 #${runPolicy.data.id} 완료`}>
              {formatNumber(runPolicy.data.fetched_count)} 라인 · {runPolicy.data.group_count} 개 그룹{' '}
              <Link
                to={`/query-runs/${runPolicy.data.id}`}
                className="font-medium text-emerald-900 underline"
              >
                그룹 보기 →
              </Link>
            </Notice>
          </div>
        )}
        {runPolicy.isError && runPolicy.variables?.id === policy.policy_id && (
          <div className="mt-3">
            <ErrorBlock error={runPolicy.error} />
          </div>
        )}
      </div>

      <footer className="flex flex-wrap gap-2 border-t border-slate-100 px-5 py-3">
        <Button
          variant="primary"
          size="sm"
          disabled={!write.allowed || !policy.active || runPolicy.isPending}
          title={
            write.reason ??
            (policy.active
              ? '이 정책의 LogQL 로 Loki 를 지금 조회합니다.'
              : '비활성 정책은 실행할 수 없습니다.')
          }
          onClick={() => runPolicy.mutate({ id: policy.policy_id, payload: {} })}
        >
          {runPolicy.isPending && runPolicy.variables?.id === policy.policy_id ? (
            <>
              <Spinner className="size-4 border-sky-200 border-t-white" />
              조회 중…
            </>
          ) : (
            <>
              <PlayIcon />
              실행
            </>
          )}
        </Button>

        <CardLink to={`/dashboard/${policy.policy_id}`}>대시보드</CardLink>
        {lastRun ? (
          <CardLink to={`/query-runs/${lastRun.id}`}>그룹 보기</CardLink>
        ) : (
          <span
            className="inline-flex cursor-not-allowed items-center rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-300"
            title="실행 이력이 없어 볼 그룹이 없습니다."
          >
            그룹 보기
          </span>
        )}
        <CardLink to={`/policies?policy=${policy.policy_id}`}>정책 설정</CardLink>
      </footer>
    </section>
  );
}

function CardLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-800 hover:bg-slate-50"
    >
      {children}
    </Link>
  );
}

// ------------------------------------------------------- summary 미배포 폴백

/**
 * `GET /api/dashboard/summary` 가 없는 백엔드용 축소 카드.
 *
 * 정책 목록만으로 만들 수 있는 것만 보여준다 — 미분석 수·24h 건수는 이 경로로는 구할 수
 * 없으므로 **지어내지 않고** 비운다. 값이 없다는 사실 자체가 정보다.
 */
function SummaryFallback() {
  const policiesQuery = usePolicies();

  return (
    <div className="space-y-6">
      <Notice tone="warning" title="통합 대시보드 API 를 아직 쓸 수 없습니다">
        <code className="rounded bg-white/60 px-1">GET /api/dashboard/summary</code> 가 응답하지
        않습니다. 백엔드에 이 경로가 올라오면 정책별 미분석 신규 그룹 수·24시간 오류 건수·최근
        실행 요약이 여기에 표시됩니다. 그 전에는 아래 목록에서 정책별 대시보드로 들어가십시오.
      </Notice>

      {policiesQuery.isPending && <LoadingBlock />}
      {policiesQuery.isError && <ErrorBlock error={policiesQuery.error} />}
      {policiesQuery.data && policiesQuery.data.length === 0 && (
        <EmptyBlock>저장된 정책이 없습니다.</EmptyBlock>
      )}
      {policiesQuery.data && policiesQuery.data.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          {policiesQuery.data.map((policy) => (
            <FallbackCard key={policy.id} policy={policy} />
          ))}
        </div>
      )}
    </div>
  );
}

function FallbackCard({ policy }: { policy: PolicyRead }) {
  const schedule = policySchedule(policy);
  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          {policy.name}
          <Badge tone={policy.active ? 'success' : 'neutral'}>
            {policy.active ? '활성' : '비활성'}
          </Badge>
          <ScheduleBadge
            enabled={schedule.enabled}
            intervalMinutes={schedule.intervalMinutes}
            autoAnalyze={schedule.autoAnalyze}
          />
        </span>
      }
      description={policy.description ?? '설명이 없습니다.'}
    >
      <p className="font-mono text-xs break-all text-slate-500">{policy.logql}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <CardLink to={`/dashboard/${policy.id}`}>대시보드</CardLink>
        <CardLink to={`/policies?policy=${policy.id}`}>정책 설정</CardLink>
      </div>
    </Card>
  );
}
