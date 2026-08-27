/**
 * 정책 하나의 상세 대시보드 (`/dashboard/:policyId`).
 *
 * 정책 **전체**를 훑는 화면은 홈(`/`)의 카드 그리드다. 여기는 카드에서 하나를 골라
 * 들어오는 자리이고, 그래서 정책 선택은 상태가 아니라 **경로**다 — 링크를 붙여
 * 공유하거나 브라우저 뒤로가기로 되짚을 수 있어야 한다.
 */

import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';

import {
  useDashboardOverview,
  usePolicies,
  usePolicyQueryRuns,
  useRunPolicy,
} from '../api/queries';
import { policySchedule, type DashboardOverviewParams, type FetchWarning } from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import { ErrorTrendChart, ServiceBarChart } from '../components/chartsLazy';
import {
  AnalysisStatusBadge,
  IngestAbsentBadge,
  ScheduleBadge,
  SeverityBadge,
} from '../components/StatusBadges';
import {
  Badge,
  Button,
  Card,
  EmptyBlock,
  ErrorBlock,
  Field,
  LoadingBlock,
  Notice,
  PageHeader,
  PlayIcon,
  Select,
  Spinner,
  Stat,
  TableWrap,
  Td,
  Th,
} from '../components/ui';
import {
  formatDateTime,
  formatNumber,
  formatRatio,
  formatRelative,
  ingestAbsentWarnings,
  truncate,
  warningCodeLabel,
} from '../lib/format';

/** 기간 프리셋. 서버가 `max_query_range_minutes` 로 상한을 강제하므로 UI 는 편의일 뿐이다. */
const RANGES = [
  { minutes: 60, label: '최근 1시간', step: 300 },
  { minutes: 6 * 60, label: '최근 6시간', step: 900 },
  { minutes: 24 * 60, label: '최근 24시간', step: 3600 },
  { minutes: 3 * 24 * 60, label: '최근 3일', step: 3600 },
];

export function DashboardPage() {
  const [rangeIndex, setRangeIndex] = useState(0);
  const params = useParams<{ policyId?: string }>();
  const navigate = useNavigate();
  const write = useWriteAccess();

  // 정책 선택은 상태가 아니라 경로다. `/dashboard` (id 없음)는 전체 조회 결과를 본다.
  const parsed = params.policyId ? Number(params.policyId) : NaN;
  const policyId = Number.isFinite(parsed) ? parsed : null;

  const policiesQuery = usePolicies();
  const runPolicy = useRunPolicy();

  const range = RANGES[rangeIndex];

  // 파라미터를 rangeIndex/policyId 에서만 파생시켜야 캐시 키가 매 렌더 바뀌지 않는다.
  const overviewParams = useMemo<DashboardOverviewParams>(() => {
    const end = new Date();
    const start = new Date(end.getTime() - range.minutes * 60_000);
    return {
      ...(policyId !== null ? { policy_id: policyId } : {}),
      range_start: start.toISOString(),
      range_end: end.toISOString(),
      step_seconds: range.step,
      top: 10,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeIndex, policyId]);

  const overview = useDashboardOverview(overviewParams);
  /*
    수집 중단 경고는 **조회 회차**에 남는다 (`query_runs.warnings`). 대시보드가 보는
    overview 는 metric 쿼리 결과라 그 경고를 반드시 싣지는 않으므로, 최근 회차 한 건을
    따로 읽어 배지의 근거로 쓴다. 경로가 없는 백엔드에서는 훅이 조용히 실패하고
    (retry 없음) 배지만 사라진다.
  */
  const recentRuns = usePolicyQueryRuns(policyId, 1);

  const allPolicies = policiesQuery.data ?? [];
  const activePolicies = allPolicies.filter((policy) => policy.active);
  // 비활성 정책의 대시보드로 직접 들어올 수 있다 — 목록에 없다고 빈 화면을 주지 않는다.
  const selectedPolicy = allPolicies.find((policy) => policy.id === policyId) ?? null;
  const schedule = selectedPolicy ? policySchedule(selectedPolicy) : null;

  /*
    수집 중단 경고. 세 출처를 합치고 메시지로 중복을 접는다 — 방금 누른 실행 결과, 최근
    회차, overview. 어느 하나만 보면 "실행한 직후에만 보이는 배지"나 "새로고침해야 사라지는
    배지"가 된다.

    이건 **기록**이다. 배지가 떠도 아무것도 자동으로 실행되지 않는다 (계약: 자동 트리거는
    정책의 auto_analyze_new 하나뿐).
  */
  const latestRun = recentRuns.data?.items?.[0] ?? null;
  const absentWarnings = ingestAbsentWarnings(
    runPolicy.isSuccess ? runPolicy.data.warnings : undefined,
    latestRun?.warnings,
    overview.data?.warnings,
  );

  /*
    지표 두 개는 **회차 전체 COUNT** 가 정답이다 (`group_count`·`unanalyzed_group_count`).
    필드를 아직 안 내려주는 백엔드에서만 상위 N 으로 폴백하고, 그 사실을 부제에 적는다 —
    두 상태를 같은 문구로 적으면 "그룹이 10개뿐"이라는 오해가 굳는다.
  */
  const data = overview.data;
  const groupCountIsExact = typeof data?.group_count === 'number';
  const groupCount = groupCountIsExact ? (data?.group_count ?? 0) : (data?.top_groups.length ?? 0);
  const unanalyzedIsExact = typeof data?.unanalyzed_group_count === 'number';
  const unanalyzedCount = unanalyzedIsExact
    ? (data?.unanalyzed_group_count ?? 0)
    : (data?.top_groups.filter((group) => !group.analysis_status).length ?? 0);

  /*
    유입량·오류 비율은 정책의 분모 쿼리(`baseline_query`)가 있어야 계산된다. **null 은 0 이
    아니다** — 화면은 `-` 로 쓰고, 왜 비었는지를 값 옆에 적는다 (미설정 / 실패 / 정책 미선택은
    사용자가 할 일이 서로 다르다).
  */
  const baselineMissing =
    selectedPolicy !== null && !(selectedPolicy.baseline_query ?? '').trim();
  const baselineHint =
    selectedPolicy === null
      ? '정책을 선택해야 계산합니다'
      : baselineMissing
        ? '분모 쿼리 미설정'
        : '분모 쿼리 실패 — 0 이 아닙니다';
  const showIngestChart = (data?.ingest_series?.length ?? 0) > 0;

  return (
    <div>
      <PageHeader
        title={
          selectedPolicy ? `대시보드 · ${selectedPolicy.name}` : '대시보드 · 전체'
        }
        description={
          <>
            건수와 추이는 <code className="rounded bg-slate-200 px-1">count_over_time</code> metric
            쿼리 결과입니다 — 로그 라인을 센 값이 아닙니다. 분석 상태는 그룹 id 가 아니라{' '}
            <strong>fingerprint 기준</strong>이라 이전 조회에서 분석한 오류도 "분석 완료"로 보입니다.
          </>
        }
        actions={
          <Link
            to="/"
            className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50"
          >
            통합 대시보드로
          </Link>
        }
      />

      {selectedPolicy && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Badge tone={selectedPolicy.active ? 'success' : 'neutral'}>
            {selectedPolicy.active ? '활성' : '비활성'}
          </Badge>
          {schedule && (
            <ScheduleBadge
              enabled={schedule.enabled}
              intervalMinutes={schedule.intervalMinutes}
              autoAnalyze={schedule.autoAnalyze}
            />
          )}
          <Link
            to={`/policies?policy=${selectedPolicy.id}`}
            className="text-xs font-medium text-sky-800 hover:underline"
          >
            정책 설정 →
          </Link>
        </div>
      )}

      {/*
        수집 중단 의심 — 오류가 0 건인 것과 로그 자체가 끊긴 것은 정반대의 사건인데
        화면에서는 똑같이 "조용한 정책"으로 보인다. 그래서 지표보다 위에 둔다.
      */}
      {absentWarnings.length > 0 && (
        <div className="mb-4">
          <Notice tone="danger" title="수집 중단 의심">
            <ul className="mt-1 space-y-1">
              {absentWarnings.map((warning, index) => (
                <li key={`${warning.code}-${index}`} className="flex flex-wrap items-center gap-2">
                  <IngestAbsentBadge warning={warning} />
                  <span>{warning.message}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs">
              연결에 등록한 <strong>수집 확인 대상 서비스</strong>가 조회 기간에 로그를 한 줄도
              내지 않았습니다. 오류가 0 건인 것과 로그가 끊긴 것은 다릅니다 — 수집 파이프라인
              (Alloy·Loki)을 먼저 확인하십시오. 이 경고는 <strong>기록일 뿐</strong>이며 아무것도
              자동으로 실행하지 않습니다.
            </p>
          </Notice>
        </div>
      )}

      <Card className="mb-6">
        <div className="grid gap-4 lg:grid-cols-3">
          {/*
            정책 선택과 실행 버튼은 한 동작의 두 반쪽이다 — 떨어뜨려 놓으면 "고르기만 하고
            실행을 못 찾는" 화면이 된다 (Phase 4 피드백 1번). 강조 배경으로 묶어 둔다.
          */}
          <div className="rounded-xl border border-sky-200 bg-sky-50/70 p-4 lg:col-span-2">
            <div className="flex flex-wrap items-end gap-3">
              <Field
                label="정책"
                className="min-w-56 flex-1"
                hint={
                  selectedPolicy
                    ? `상한 ${formatNumber(selectedPolicy.max_lines)} 라인 · 대표 로그 ${selectedPolicy.max_samples_per_group} 개`
                    : '선택하지 않으면 전체 조회 결과를 보여줍니다.'
                }
              >
                <Select
                  value={policyId ?? ''}
                  onChange={(event) =>
                    navigate(
                      event.target.value === '' ? '/dashboard' : `/dashboard/${event.target.value}`,
                    )
                  }
                >
                  <option value="">전체</option>
                  {activePolicies.map((policy) => (
                    <option key={policy.id} value={policy.id}>
                      {policy.name}
                    </option>
                  ))}
                  {/* 비활성 정책으로 직접 들어온 경우에도 선택 상태가 보여야 한다. */}
                  {selectedPolicy && !selectedPolicy.active && (
                    <option value={selectedPolicy.id}>{selectedPolicy.name} (비활성)</option>
                  )}
                </Select>
              </Field>

              <Button
                variant="primary"
                size="lg"
                className="mb-6"
                disabled={!write.allowed || !selectedPolicy || runPolicy.isPending}
                title={
                  write.reason ??
                  (selectedPolicy
                    ? `${selectedPolicy.name} 정책으로 Loki 를 조회합니다.`
                    : '실행할 정책을 먼저 고르십시오.')
                }
                onClick={() => {
                  if (!selectedPolicy) return;
                  runPolicy.mutate({ id: selectedPolicy.id, payload: {} });
                }}
              >
                {runPolicy.isPending ? (
                  <>
                    <Spinner className="size-4 border-sky-200 border-t-white" />
                    조회 중…
                  </>
                ) : (
                  <>
                    <PlayIcon />
                    정책 실행
                  </>
                )}
              </Button>
            </div>

            <p className="mt-1 text-xs text-slate-600">
              {!write.allowed ? (
                <>{write.reason}</>
              ) : selectedPolicy ? (
                <>
                  실행하면 이 정책의 LogQL 로 Loki 를 <strong>지금</strong> 조회하고 결과를
                  그룹으로 묶습니다. 분석(LLM 호출)은 그룹 상세에서 따로 실행합니다.
                </>
              ) : (
                <>정책을 골라야 실행할 수 있습니다.</>
              )}
            </p>
          </div>

          <Field label="기간">
            <Select
              value={rangeIndex}
              onChange={(event) => setRangeIndex(Number(event.target.value))}
            >
              {RANGES.map((item, index) => (
                <option key={item.label} value={index}>
                  {item.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        {selectedPolicy && (
          <div className="mt-4">
            <Field label="LogQL">
              <pre className="aila-scroll overflow-x-auto rounded-lg bg-slate-900 px-3 py-2 text-xs whitespace-pre text-slate-100">
                {selectedPolicy.logql}
              </pre>
            </Field>
          </div>
        )}

        {runPolicy.isPending && (
          <div className="mt-4">
            <Notice tone="info">
              <span className="inline-flex items-center gap-2">
                <Spinner />
                정책 <strong>{selectedPolicy?.name}</strong> 으로 조회하는 중입니다. 기간·라인 수
                상한은 서버가 강제합니다.
              </span>
            </Notice>
          </div>
        )}
        {runPolicy.isError && (
          <div className="mt-4">
            <ErrorBlock error={runPolicy.error} />
          </div>
        )}
        {runPolicy.isSuccess && !runPolicy.isPending && (
          <div className="mt-4">
            <Notice tone="success" title={`조회 #${runPolicy.data.id} 완료`}>
              {formatNumber(runPolicy.data.fetched_count)} 라인 조회 ·{' '}
              {formatNumber(runPolicy.data.dropped_count)} 라인 제외 ·{' '}
              {runPolicy.data.group_count} 개 그룹
              <Link
                to={`/query-runs/${runPolicy.data.id}`}
                className="ml-2 font-medium text-emerald-900 underline"
              >
                이 조회의 오류 그룹 보기 →
              </Link>
              {runPolicy.data.warnings.length > 0 && (
                <ul className="mt-1 list-disc pl-4 text-xs">
                  {runPolicy.data.warnings.map((warning, index) => (
                    <li key={`${warning.code}-${index}`}>
                      {warningCodeLabel(warning.code)} — {warning.message}
                    </li>
                  ))}
                </ul>
              )}
            </Notice>
          </div>
        )}
      </Card>

      {overview.isPending && <LoadingBlock />}
      {overview.isError && <ErrorBlock error={overview.error} />}

      {overview.data && (
        <div className="space-y-6">
          {/* 수집 중단은 위에서 이미 전용 블록으로 말했다 — 여기서 또 적지 않는다. */}
          <WarningList
            warnings={overview.data.warnings.filter(
              (warning) => warning.code !== 'ingest_absent',
            )}
          />

          {/*
            지표 타일. `group_count`·`unanalyzed_group_count` 는 **회차 전체 COUNT** 다 —
            `top_groups.length`(상위 N)를 지표 자리에 쓰면 정책이 커질수록 항상 "10"이 되어
            숫자가 상한에 붙어 버린다. 필드가 없는 옛 백엔드에서만 옛 계산으로 폴백하고,
            그 경우 부제에 "상위 N 기준"이라고 적어 두 상태를 구분한다.
          */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
            <Stat
              label="총 오류 건수 (metric)"
              value={formatNumber(overview.data.total_errors)}
              sub={range.label}
              tone="accent"
            />
            <Stat
              label="유입량 (분모)"
              value={
                overview.data.ingest_total == null ? (
                  <span className="text-slate-400">-</span>
                ) : (
                  formatNumber(overview.data.ingest_total)
                )
              }
              sub={overview.data.ingest_total == null ? baselineHint : '같은 기간 전체 로그 (metric)'}
            />
            <Stat
              label="오류 비율"
              value={
                overview.data.error_ratio == null ? (
                  <span className="text-slate-400">-</span>
                ) : (
                  formatRatio(overview.data.error_ratio)
                )
              }
              sub={overview.data.error_ratio == null ? baselineHint : '오류 ÷ 유입량'}
            />
            <Stat
              label="오류 그룹"
              value={formatNumber(groupCount)}
              sub={groupCountIsExact ? '이 회차 전체' : `상위 ${overview.data.top_groups.length}개 기준`}
            />
            <Stat
              label="미분석 그룹"
              value={formatNumber(unanalyzedCount)}
              sub={
                unanalyzedIsExact
                  ? '이 회차 전체 · fingerprint 기준'
                  : `상위 ${overview.data.top_groups.length}개 기준 · fingerprint`
              }
            />
            <Stat
              label="영향 서비스"
              value={formatNumber(overview.data.by_service.length)}
              sub="라벨 기준"
            />
          </div>

          {/* 분모 쿼리가 없으면 유입량·비율 칸이 왜 비어 있는지 같은 자리에 적는다. */}
          {baselineMissing && selectedPolicy && (
            <Notice tone="neutral" title="분모 쿼리가 설정되지 않았습니다">
              유입량과 오류 비율은 <strong>분모 쿼리</strong>(오류 셀렉터와 같은 라벨 범위의 전체
              로그를 세는 쿼리)가 있어야 계산합니다. 값이 <code>-</code> 인 것은{' '}
              <strong>0 이라는 뜻이 아닙니다</strong>.{' '}
              <Link
                to={`/policies/${selectedPolicy.id}/edit`}
                className="font-medium text-sky-800 underline"
              >
                정책 수정
              </Link>{' '}
              에서 <strong>분모 쿼리</strong>를 채우면 이 자리에 표시됩니다.
            </Notice>
          )}

          <div className="grid gap-6 lg:grid-cols-3">
            <Card
              title="시간대별 오류 건수"
              description={`${formatDateTime(overview.data.range_start)} ~ ${formatDateTime(
                overview.data.range_end,
              )} · ${overview.data.step_seconds}초 간격 · metric 쿼리 기준`}
              className={showIngestChart ? 'lg:col-span-2' : 'lg:col-span-3'}
            >
              <ErrorTrendChart points={overview.data.series} />
            </Card>

            {/*
              유입량은 오류와 **눈금이 다르다** (보통 두 자릿수 크다). 한 축에 겹치면 오류
              곡선이 바닥에 눌려 모양이 사라지므로 차트를 나누고 색으로만 묶는다.
            */}
            {showIngestChart && (
              <Card
                title="유입량 추이 (분모)"
                description="오류와 눈금이 달라 축을 나눴습니다. 같은 기간·같은 간격입니다."
              >
                <ErrorTrendChart
                  points={overview.data.ingest_series}
                  height={260}
                  label="유입 건수"
                  tone="series2"
                />
              </Card>
            )}
          </div>

          <div className="grid gap-6 lg:grid-cols-5">
            <Card
              title="서비스별 오류 건수"
              description="라인 수가 아니라 metric 집계입니다."
              className="lg:col-span-2"
            >
              <ServiceBarChart data={overview.data.by_service} />
            </Card>

            <Card
              title={`상위 오류 그룹 (${formatNumber(overview.data.top_groups.length)}개)`}
              description={
                <>
                  이 표는 <strong>상위 몇 개</strong>만 보여줍니다 — 회차 전체 그룹 수는 위의{' '}
                  <strong>오류 그룹</strong> 지표입니다. 분석 상태는 fingerprint 기준이라 이미
                  분석된 오류를 중복 요청(=중복 과금)하지 않습니다.
                </>
              }
              className="lg:col-span-3"
            >
              {overview.data.top_groups.length === 0 ? (
                <EmptyBlock>이 기간에 묶인 오류 그룹이 없습니다.</EmptyBlock>
              ) : (
                <TableWrap minWidth="36rem">
                  <thead>
                    <tr>
                      <Th>메시지 · 서비스</Th>
                      <Th align="right" className="whitespace-nowrap">
                        발생 수
                      </Th>
                      <Th className="whitespace-nowrap">마지막 발생</Th>
                      <Th className="whitespace-nowrap">분석 상태</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.data.top_groups.map((group) => (
                      <tr key={group.id} className="hover:bg-slate-50">
                        <Td>
                          <Link
                            to={`/error-groups/${group.id}`}
                            className="font-medium text-sky-800 hover:underline"
                            title={group.normalized_message}
                          >
                            {truncate(group.normalized_message, 90)}
                          </Link>
                          <p className="mt-0.5 text-xs text-slate-500">
                            {group.service ?? '(서비스 라벨 없음)'}
                            {group.environment ? ` · ${group.environment}` : ''}
                            {group.error_type ? ` · ${group.error_type}` : ''}
                          </p>
                        </Td>
                        <Td align="right" className="font-semibold text-slate-900">
                          {formatNumber(group.count)}
                        </Td>
                        <Td className="whitespace-nowrap">
                          <span title={formatDateTime(group.last_seen)}>
                            {formatRelative(group.last_seen)}
                          </span>
                        </Td>
                        <Td className="whitespace-nowrap">
                          <div className="flex flex-col items-start gap-1">
                            <AnalysisStatusBadge status={group.analysis_status} />
                            {group.latest_severity && (
                              <SeverityBadge severity={group.latest_severity} />
                            )}
                          </div>
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </TableWrap>
              )}
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

export function WarningList({ warnings }: { warnings: FetchWarning[] }) {
  if (warnings.length === 0) return null;
  return (
    <Notice tone="warning" title="조회 경고">
      <ul className="mt-1 space-y-1">
        {warnings.map((warning, index) => (
          <li key={`${warning.code}-${index}`}>
            <strong>{warningCodeLabel(warning.code)}</strong>
            {warning.count != null && ` (${formatNumber(warning.count)}건)`} — {warning.message}
          </li>
        ))}
      </ul>
    </Notice>
  );
}
