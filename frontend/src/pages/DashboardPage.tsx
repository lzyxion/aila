/**
 * 정책 하나의 상세 대시보드 (`/dashboard/:policyId`).
 *
 * 정책 **전체**를 훑는 화면은 홈(`/`)의 카드 그리드다. 여기는 카드에서 하나를 골라
 * 들어오는 자리이고, 그래서 정책 선택은 상태가 아니라 **경로**다 — 링크를 붙여
 * 공유하거나 브라우저 뒤로가기로 되짚을 수 있어야 한다.
 */

import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';

import { useDashboardOverview, usePolicies, useRunPolicy } from '../api/queries';
import { policySchedule, type DashboardOverviewParams, type FetchWarning } from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import { ErrorTrendChart, ServiceBarChart } from '../components/chartsLazy';
import { AnalysisStatusBadge, ScheduleBadge, SeverityBadge } from '../components/StatusBadges';
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
import { formatDateTime, formatNumber, formatRelative, truncate, warningCodeLabel } from '../lib/format';

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

  const allPolicies = policiesQuery.data ?? [];
  const activePolicies = allPolicies.filter((policy) => policy.active);
  // 비활성 정책의 대시보드로 직접 들어올 수 있다 — 목록에 없다고 빈 화면을 주지 않는다.
  const selectedPolicy = allPolicies.find((policy) => policy.id === policyId) ?? null;
  const schedule = selectedPolicy ? policySchedule(selectedPolicy) : null;

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
          <WarningList warnings={overview.data.warnings} />

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="총 오류 건수 (metric)"
              value={formatNumber(overview.data.total_errors)}
              sub={range.label}
              tone="accent"
            />
            <Stat
              label="오류 그룹"
              value={formatNumber(overview.data.top_groups.length)}
              sub="상위 그룹만 표시"
            />
            <Stat
              label="영향 서비스"
              value={formatNumber(overview.data.by_service.length)}
              sub="라벨 기준"
            />
            <Stat
              label="미분석 그룹"
              value={formatNumber(
                overview.data.top_groups.filter((group) => !group.analysis_status).length,
              )}
              sub="fingerprint 기준"
            />
          </div>

          <Card
            title="시간대별 오류 건수"
            description={`${formatDateTime(overview.data.range_start)} ~ ${formatDateTime(
              overview.data.range_end,
            )} · ${overview.data.step_seconds}초 간격 · metric 쿼리 기준`}
          >
            <ErrorTrendChart points={overview.data.series} />
          </Card>

          <div className="grid gap-6 lg:grid-cols-5">
            <Card
              title="서비스별 오류 건수"
              description="라인 수가 아니라 metric 집계입니다."
              className="lg:col-span-2"
            >
              <ServiceBarChart data={overview.data.by_service} />
            </Card>

            <Card
              title="상위 오류 그룹"
              description="분석 상태는 fingerprint 기준 — 이미 분석된 오류를 중복 요청(=중복 과금)하지 않기 위해서입니다."
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
