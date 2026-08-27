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
 * 표시 규칙(계약) — 문구는 지우지 않고 ⓘ(`InfoTip`) 로 옮겼다:
 * - `total_errors_24h` 는 `count_over_time` metric 기준이고 로그 라인 수가 아니다.
 *   metric 쿼리에 실패하면 **null** 이며 0 이 아니다 — 화면은 `-` 로 쓴다.
 * - `unanalyzed_group_count` 는 그룹 id 가 아니라 **fingerprint 기준**이다.
 * - 실행은 쓰기 동작이라 `admin` 만 누를 수 있다. 판정은 서버가 한다.
 *
 * 카드 안의 **정보 무게 순서**는 수집 중단 > 미분석 > 오류 수다. 수집 중단은 "오류가
 * 0 건"과 정반대의 사건인데 카드에서는 둘 다 조용해 보이므로, 축약 대상에서 제외하고
 * 배지 + 문장을 머리에 그대로 둔다.
 */

import { useMemo, useState } from 'react';
import { Link } from 'react-router';

import { isEndpointMissing } from '../api/client';
import { useDashboardSummary, usePolicies, useRunPolicy } from '../api/queries';
import type { DashboardSummaryPolicy, PolicyRead } from '../api/types';
import { policySchedule } from '../api/types';
import { Sparkline } from '../components/chartsLazy';
import { DailyLimitGauge } from '../components/DailyLimitGauge';
import {
  DashboardIcon,
  ErrorGroupIcon,
  GroupCountIcon,
  PolicyIcon,
  ScheduleIcon,
} from '../components/icons';
import { IngestAbsentBadge, QueryRunStatusBadge, ScheduleBadge } from '../components/StatusBadges';
import {
  Badge,
  Button,
  ButtonLink,
  Card,
  Code,
  EmptyBlock,
  ErrorBlock,
  Field,
  Input,
  InfoTip,
  LoadingBlock,
  Notice,
  PageHeader,
  PlayIcon,
  Select,
  SkeletonStats,
  Spinner,
  Stat,
  TextLink,
  cx,
} from '../components/ui';
import { useWriteAccess } from '../auth/AuthContext';
import {
  formatDateTime,
  formatNumber,
  formatRelative,
  ingestAbsentWarnings,
  warningCodeLabel,
} from '../lib/format';
import { AllErrorGroupsPanel } from './ErrorGroupsPage';

type SortKey = 'unanalyzed' | 'errors' | 'recent' | 'name';

const SORTS: Array<{ key: SortKey; label: string }> = [
  { key: 'unanalyzed', label: '미분석 신규 그룹 많은 순' },
  { key: 'errors', label: '24h 오류 많은 순' },
  { key: 'recent', label: '최근 실행순' },
  { key: 'name', label: '정책명순' },
];

/** 화면 곳곳에서 같은 뜻으로 반복되는 계약 문구 — 한 곳에서 만들어 InfoTip 으로 나른다. */
const METRIC_NOTE = (
  <>
    24시간 오류 건수는 <Code>count_over_time</Code> metric 쿼리 결과라{' '}
    <strong>로그 라인 수가 아닙니다</strong>.
    <span className="mt-1.5 block">
      metric 쿼리가 실패하면 값은 0 이 아니라 <strong>없음</strong>이며 화면에는 <Code>-</Code> 로
      나옵니다.
    </span>
  </>
);

const UNANALYZED_NOTE = (
  <>
    최근 <strong>성공한</strong> 조회의 그룹 중 <strong>fingerprint 기준</strong>으로 분석 이력이
    전혀 없는 수입니다.
    <span className="mt-1.5 block">
      그룹 id 기준이 아니므로 이전 회차에서 분석한 오류는 여기 세지 않습니다. 실패한 분석도
      "이력 있음"입니다.
    </span>
  </>
);

export function HomePage() {
  const summaryQuery = useDashboardSummary();
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortKey>('unanalyzed');

  const cards = useMemo(
    () => sortCards(filterCards(summaryQuery.data?.policies ?? [], search), sort),
    [search, sort, summaryQuery.data],
  );

  const totals = useMemo(
    () => summarise(summaryQuery.data?.policies ?? []),
    [summaryQuery.data],
  );

  return (
    <div>
      <PageHeader
        title="통합 대시보드"
        description="정책별 최근 상태 요약입니다."
        info={
          <>
            {UNANALYZED_NOTE}
            <span className="mt-1.5 block">{METRIC_NOTE}</span>
          </>
        }
        actions={
          summaryQuery.data && (
            <span className="text-xs text-faint tabular-nums">
              생성 {formatDateTime(summaryQuery.data.generated_at)}
            </span>
          )
        }
      />

      {/*
        오늘의 분석 한도. 카드를 훑기 전에 "지금 분석을 몇 번 더 돌릴 수 있는가"가 보여야
        한다 — 한도를 모르고 실행을 누르면 429 가 처음 알려주는 셈이 된다. 백엔드에 아직
        경로가 없으면 이 줄은 통째로 사라진다.
      */}
      <div className="mb-6">
        <DailyLimitGauge />
      </div>

      {/* 요약 타일은 4칸으로 자리가 정해져 있다 — 스켈레톤이 거짓 신호를 주지 않는다. */}
      {summaryQuery.isPending && <SkeletonStats count={4} label="정책 요약을 불러오는 중" />}

      {/* 백엔드에 아직 이 경로가 없으면 실패로 표시하지 않고 축소 카드로 물러난다. */}
      {summaryQuery.isError &&
        (isEndpointMissing(summaryQuery.error) ? (
          <SummaryFallback />
        ) : (
          <ErrorBlock error={summaryQuery.error} />
        ))}

      {summaryQuery.data && (
        <>
          {/*
            요약 카드 줄 — 카드 그리드를 훑기 전에 "전체가 어떤 상태인가"를 네 숫자로 먼저
            답한다. 정책이 스무 개가 되면 카드만으로는 합계를 사람이 암산해야 한다.
          */}
          <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="전체 정책"
              icon={PolicyIcon}
              value={formatNumber(totals.policyCount)}
              sub={`활성 ${formatNumber(totals.activeCount)} · 비활성 ${formatNumber(
                totals.policyCount - totals.activeCount,
              )}`}
            />
            <Stat
              label="활성 스케줄"
              icon={ScheduleIcon}
              value={formatNumber(totals.scheduledCount)}
              sub="주기 조회가 켜진 정책"
            />
            <Stat
              label="24h 총 오류"
              icon={ErrorGroupIcon}
              value={
                totals.errors24h === null ? (
                  <span className="text-faint">-</span>
                ) : (
                  formatNumber(totals.errors24h)
                )
              }
              sub={
                totals.errors24h === null
                  ? 'metric 실패 · 0 건 아님'
                  : totals.metricFailedCount > 0
                    ? `${totals.metricFailedCount}개 정책 제외 (0 아님)`
                    : 'metric 기준'
              }
              info={
                <>
                  {METRIC_NOTE}
                  {totals.errors24h === null ? (
                    <span className="mt-1.5 block">
                      <strong>전 정책의 metric 쿼리가 실패했습니다</strong> — 0 건이라는 뜻이
                      아닙니다.
                    </span>
                  ) : totals.metricFailedCount > 0 ? (
                    <span className="mt-1.5 block">
                      {totals.metricFailedCount}개 정책은 metric 실패로 <strong>합계에서
                      제외</strong>했습니다 (0 으로 더하지 않습니다).
                    </span>
                  ) : null}
                </>
              }
            />
            <Stat
              label="미분석 신규 그룹"
              icon={GroupCountIcon}
              value={formatNumber(totals.unanalyzed)}
              sub="아무도 보지 않은 오류"
              info={UNANALYZED_NOTE}
              tone="accent"
            />
          </div>

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
              <p className="mb-2 text-xs text-muted tabular-nums">
                {cards.length === totals.policyCount
                  ? `정책 ${formatNumber(totals.policyCount)}개`
                  : `정책 ${formatNumber(cards.length)} / ${formatNumber(totals.policyCount)}개`}
              </p>
            </div>
          </Card>

          {summaryQuery.data.policies.length === 0 ? (
            <EmptyBlock icon={PolicyIcon}>
              저장된 정책이 없습니다. <TextLink to="/policies">분석 정책</TextLink> 화면에서 하나를
              만드십시오.
            </EmptyBlock>
          ) : cards.length === 0 ? (
            <EmptyBlock icon={PolicyIcon}>&quot;{search}&quot; 에 해당하는 정책이 없습니다.</EmptyBlock>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
              {cards.map((policy) => (
                <PolicyCard key={policy.policy_id} policy={policy} />
              ))}
            </div>
          )}

          {/*
            하단 전체 오류 그룹 — 카드가 답하는 "어느 정책이 시끄러운가" 다음에 오는 질문,
            "그래서 무슨 오류인가"를 여기서 받는다.
          */}
          <div className="mt-6">
            <AllErrorGroupsPanel pageSize={10} compact />
          </div>
        </>
      )}
    </div>
  );
}

/**
 * 요약 카드 줄의 네 숫자.
 *
 * `total_errors_24h` 의 **null 은 0 이 아니다** — 합계에서 빼고, 몇 개가 빠졌는지 따로
 * 적는다. null 을 0 으로 더하면 "오류가 줄었다"로 읽히고, 전부 null 이면 합계 자체가
 * `-` 여야 한다(0 건이라고 단언할 근거가 없다).
 */
function summarise(policies: DashboardSummaryPolicy[]): {
  policyCount: number;
  activeCount: number;
  scheduledCount: number;
  errors24h: number | null;
  metricFailedCount: number;
  unanalyzed: number;
} {
  const counted = policies.filter((policy) => policy.total_errors_24h !== null);
  return {
    policyCount: policies.length,
    activeCount: policies.filter((policy) => policy.active).length,
    scheduledCount: policies.filter((policy) => policy.schedule_enabled && policy.active).length,
    errors24h: counted.length
      ? counted.reduce((acc, policy) => acc + (policy.total_errors_24h ?? 0), 0)
      : null,
    metricFailedCount: policies.length - counted.length,
    unanalyzed: policies.reduce((acc, policy) => acc + policy.unanalyzed_group_count, 0),
  };
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

/**
 * 정책 카드.
 *
 * Phase 8 밀도 정리에서 카드가 말하는 것을 네 가지로 줄였다 — **수집 중단 경고 · 미분석
 * 신규 그룹 · 24h 추이 · 최근 실행 한 줄**, 그리고 실행 버튼. 지운 것은 없고, 사라진
 * 문장은 전부 ⓘ 안에 있다. 예외는 수집 중단 경고 하나로, 이건 축약하지 않는다.
 */
function PolicyCard({ policy }: { policy: DashboardSummaryPolicy }) {
  const write = useWriteAccess();
  const runPolicy = useRunPolicy();
  const lastRun = policy.last_run;
  const unanalyzed = policy.unanalyzed_group_count;
  // 백엔드가 아직 이 필드를 안 내려주면 `undefined` 다 — 빈 배열과 같게 다룬다(오류 아님).
  const series = policy.series_24h ?? [];
  /*
    수집 중단 의심. `last_run.warnings` 에 실려 온다 — 카드에서 이 사실이 다른 경고와 같은
    무게로 묻히면 안 된다. "오류 0 건"과 "로그가 끊겼다"는 정반대의 사건인데 카드에서는
    둘 다 조용해 보이기 때문이다. 아래 일반 경고 목록에서는 빼서 같은 말을 두 번 하지 않는다.
  */
  const absentWarnings = ingestAbsentWarnings(lastRun?.warnings, policy.warnings);
  const otherRunWarnings = (lastRun?.warnings ?? []).filter(
    (warning) => warning.code !== 'ingest_absent',
  );
  const otherPolicyWarnings = policy.warnings.filter(
    (warning) => warning.code !== 'ingest_absent',
  );
  const otherWarnings = [...otherRunWarnings, ...otherPolicyWarnings];
  const running = runPolicy.isPending && runPolicy.variables?.id === policy.policy_id;

  return (
    <section
      className={cx(
        'flex flex-col rounded-xl border bg-surface shadow-sm shadow-slate-900/5',
        // 색은 보조 신호다 — 같은 사실이 배지와 문장으로도 카드 안에 적혀 있다.
        absentWarnings.length > 0
          ? 'border-rose-300 dark:border-rose-800'
          : unanalyzed > 0
            ? 'border-amber-300 dark:border-amber-800'
            : 'border-line',
        !policy.active && 'opacity-75',
      )}
    >
      <header className="border-b border-line-soft px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <h2 className="min-w-0 text-base font-semibold text-ink">
            <Link to={`/dashboard/${policy.policy_id}`} className="hover:underline">
              {policy.name}
            </Link>
          </h2>
          <span className="text-xs text-faint tabular-nums">#{policy.policy_id}</span>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <Badge tone={policy.active ? 'success' : 'neutral'}>
            {policy.active ? '활성' : '비활성'}
          </Badge>
          <ScheduleBadge
            enabled={policy.schedule_enabled}
            intervalMinutes={policy.schedule_interval_minutes}
          />
          {absentWarnings.map((warning, index) => (
            <IngestAbsentBadge key={`${warning.code}-${index}`} warning={warning} compact />
          ))}
        </div>
        {/*
          수집 중단은 축약 대상이 아니다 — 카드에서 가장 무거운 사실이라 문장을 그대로 둔다.
        */}
        {absentWarnings.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-xs text-rose-800 dark:text-rose-300">
            {absentWarnings.map((warning, index) => (
              <li key={`msg-${warning.code}-${index}`}>{warning.message}</li>
            ))}
          </ul>
        )}
      </header>

      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] gap-px bg-line-soft">
        {/* 이 화면에서 가장 중요한 숫자 — 카드에서 가장 크게 둔다. */}
        <div className="bg-surface px-5 py-3">
          <p className="flex items-center gap-1 text-xs font-medium text-muted">
            <span className="min-w-0 truncate">미분석 신규 그룹</span>
            <InfoTip label="미분석 신규 그룹 설명 보기" title="미분석 신규 그룹">
              {UNANALYZED_NOTE}
            </InfoTip>
          </p>
          <p
            className={cx(
              'mt-1 text-3xl font-bold tabular-nums',
              unanalyzed > 0 ? 'text-amber-700 dark:text-amber-300' : 'text-faint',
            )}
          >
            {formatNumber(unanalyzed)}
          </p>
        </div>

        {/*
          24h 오류 수와 추이를 한 칸에 합쳤다 — 같은 `count_over_time` 응답에서 온 값이라
          따로 두면 카드에 큰 숫자가 둘이 되고, 정작 "늘고 있는가"가 묻힌다. 포인트가 없으면
          선을 그리지 않는다 (평평한 선은 "오류가 없었다"로 읽힌다).
        */}
        <div className="bg-surface px-5 py-3">
          <div className="flex items-center justify-between gap-2">
            <p className="flex items-center gap-1 text-xs font-medium text-muted">
              <span className="min-w-0 truncate">24h 오류 · 추이</span>
              <InfoTip label="24h 오류 건수 설명 보기" title="24h 오류 건수" align="end">
                {METRIC_NOTE}
                <span className="mt-1.5 block">
                  추이는 합계와 <strong>같은 응답</strong>의 시간당 포인트입니다. 축은 그리지
                  않습니다 — 모양만 보는 자리입니다.
                </span>
              </InfoTip>
            </p>
            <p className="text-xl font-bold text-ink tabular-nums">
              {policy.total_errors_24h === null ? (
                <span className="text-faint">-</span>
              ) : (
                formatNumber(policy.total_errors_24h)
              )}
            </p>
          </div>
          {series.length > 0 ? (
            <div className="mt-1">
              <Sparkline points={series} height={36} />
            </div>
          ) : (
            <p
              className={cx(
                'mt-1.5 text-xs',
                policy.total_errors_24h === null
                  ? 'text-amber-700 dark:text-amber-300'
                  : 'text-faint',
              )}
            >
              {policy.total_errors_24h === null
                ? 'metric 쿼리 실패 · 0 건 아님'
                : '추이 데이터 없음'}
            </p>
          )}
        </div>
      </div>

      <div className="flex-1 border-t border-line-soft px-5 py-3">
        {lastRun ? (
          <div className="space-y-1.5">
            {/* 최근 실행은 한 줄이다 — 상태·시각·규모가 한 눈에 읽히면 그걸로 충분하다. */}
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <QueryRunStatusBadge status={lastRun.status} />
              <span className="text-ink-soft" title={formatDateTime(lastRun.started_at)}>
                {formatRelative(lastRun.started_at)}
              </span>
              <span className="tabular-nums">
                {formatNumber(lastRun.fetched_count)} 라인 ·{' '}
                <strong className="text-ink-soft">{formatNumber(lastRun.group_count)}</strong> 그룹
              </span>
              <span className="text-faint tabular-nums">#{lastRun.id}</span>
            </div>
            {otherWarnings.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                {otherWarnings.map((warning, index) => (
                  <Badge key={`${warning.code}-${index}`} tone="warning" title={warning.message}>
                    {warningCodeLabel(warning.code)}
                  </Badge>
                ))}
                {/* 경고 문장은 지우지 않고 여기로 옮긴다 (배지는 코드만 말한다). */}
                <InfoTip label="조회 경고 상세 보기" title="조회 경고" align="end">
                  {otherWarnings.map((warning, index) => (
                    <span key={`tip-${warning.code}-${index}`} className={index ? 'mt-1.5 block' : 'block'}>
                      <strong>{warningCodeLabel(warning.code)}</strong> — {warning.message}
                    </span>
                  ))}
                </InfoTip>
              </div>
            )}
          </div>
        ) : (
          <p className="text-xs text-muted">아직 실행 이력이 없습니다.</p>
        )}

        {runPolicy.isSuccess && runPolicy.variables?.id === policy.policy_id && (
          <div className="mt-3">
            <Notice tone="success" title={`조회 #${runPolicy.data.id} 완료`}>
              {formatNumber(runPolicy.data.fetched_count)} 라인 · {runPolicy.data.group_count} 개 그룹{' '}
              <TextLink to={`/query-runs/${runPolicy.data.id}`}>그룹 보기 →</TextLink>
            </Notice>
          </div>
        )}
        {runPolicy.isError && runPolicy.variables?.id === policy.policy_id && (
          <div className="mt-3">
            <ErrorBlock error={runPolicy.error} />
          </div>
        )}
      </div>

      <footer className="flex flex-wrap gap-2 border-t border-line-soft px-5 py-3">
        <Button
          variant="primary"
          size="sm"
          disabled={!write.allowed || !policy.active || runPolicy.isPending}
          title={
            write.reason ??
            (policy.active
              ? '이 정책의 쿼리로 로그 소스를 지금 조회합니다.'
              : '비활성 정책은 실행할 수 없습니다.')
          }
          onClick={() => runPolicy.mutate({ id: policy.policy_id, payload: {} })}
        >
          {running ? (
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

        <ButtonLink to={`/dashboard/${policy.policy_id}`} size="sm">
          <DashboardIcon aria-hidden className="size-3.5" />
          대시보드
        </ButtonLink>
        {lastRun ? (
          <ButtonLink to={`/query-runs/${lastRun.id}`} size="sm" variant="ghost">
            그룹 보기
          </ButtonLink>
        ) : (
          <span
            className="inline-flex cursor-not-allowed items-center rounded-lg px-2.5 py-1.5 text-xs font-medium text-faint"
            title="실행 이력이 없어 볼 그룹이 없습니다."
          >
            그룹 보기
          </span>
        )}
      </footer>
    </section>
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
        <Code>GET /api/dashboard/summary</Code> 가 응답하지 않습니다. 백엔드에 이 경로가 올라오면
        정책별 미분석 신규 그룹 수·24시간 오류 건수·최근 실행 요약이 여기에 표시됩니다. 그 전에는
        아래 목록에서 정책별 대시보드로 들어가십시오.
      </Notice>

      {policiesQuery.isPending && <LoadingBlock />}
      {policiesQuery.isError && <ErrorBlock error={policiesQuery.error} />}
      {policiesQuery.data && policiesQuery.data.length === 0 && (
        <EmptyBlock icon={PolicyIcon}>저장된 정책이 없습니다.</EmptyBlock>
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
      <p className="font-mono text-xs break-all text-muted">{policy.query}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <ButtonLink to={`/dashboard/${policy.id}`} size="sm">
          <DashboardIcon aria-hidden className="size-3.5" />
          대시보드
        </ButtonLink>
        <ButtonLink to={`/policies?policy=${policy.id}`} size="sm" variant="ghost">
          정책 설정
        </ButtonLink>
      </div>
    </Card>
  );
}
