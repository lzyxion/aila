/**
 * 분석 정책 (`/policies`) — **좌측 목록 + 우측 조회 전용 상세**.
 *
 * 추가·수정은 여기 없다. `/policies/new` · `/policies/:id/edit` 로 나갔다 — 정책은 입력
 * 항목이 열 개가 넘고 미리보기 결과까지 붙는데, 그 폼을 목록 옆에 두면 "지금 무엇을
 * 고치는 중인가"가 스크롤 밖으로 밀린다.
 *
 * 이 화면이 답하는 질문은 "이 정책이 무엇을 어떻게 잡고 있나"이고, 그래서 상세는 설정
 * 값·스케줄 배지·실행 이력만 보여준다. 비활성화/재활성만 여기 남긴다 — 목록에서 곧바로
 * 눌러야 의미가 있는 한 번짜리 동작이고, 폼을 열 이유가 없기 때문이다.
 */

import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';

import { isEndpointMissing } from '../api/client';
import {
  useDeactivatePolicy,
  usePolicies,
  usePolicyQueryRuns,
  useUpdatePolicy,
} from '../api/queries';
import { policySchedule, type PolicyRead } from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import {
  QueryRunStatusBadge,
  ScheduleBadge,
  TriggeredByBadge,
} from '../components/StatusBadges';
import {
  Badge,
  Button,
  Card,
  EmptyBlock,
  ErrorBlock,
  LoadingBlock,
  Notice,
  PageHeader,
  PlayIcon,
  TableWrap,
  Td,
  Th,
  cx,
} from '../components/ui';
import {
  formatDateTime,
  formatIntervalMinutes,
  formatNumber,
  formatRelative,
  truncate,
  warningCodeLabel,
} from '../lib/format';
import { AUTO_ANALYZE_COST_NOTE } from './PolicyEditPage';

export function PoliciesPage() {
  const write = useWriteAccess();
  const [searchParams] = useSearchParams();
  const policiesQuery = usePolicies();
  const updatePolicy = useUpdatePolicy();
  const deactivatePolicy = useDeactivatePolicy();

  const [selectedId, setSelectedId] = useState<number | null>(null);

  // 목록은 활성·비활성을 **모두** 보여준다. 비활성이 사라지면 되살릴 경로도 같이 사라진다.
  const allPolicies = policiesQuery.data ?? [];
  const selectedPolicy = allPolicies.find((policy) => policy.id === selectedId) ?? null;

  // 대시보드 카드·저장 직후 리다이렉트가 `?policy=` 로 들어온다 — 그 정책을 열어 준다.
  const requestedId = Number(searchParams.get('policy'));
  useEffect(() => {
    if (allPolicies.length === 0) return;
    const wanted = allPolicies.find((policy) => policy.id === requestedId);
    if (wanted && wanted.id !== selectedId) {
      setSelectedId(wanted.id);
      return;
    }
    if (selectedId === null) setSelectedId(allPolicies[0].id);
  }, [allPolicies, requestedId, selectedId]);

  return (
    <div>
      <PageHeader
        title="분석 정책"
        description={
          <>
            정책은 LogQL 한 줄이 아니라 <strong>실행 한도를 포함한 묶음</strong>입니다. 이 화면은
            조회 전용이고, 추가·수정은 전용 페이지에서 합니다 — 저장 전 미리보기도 거기 있습니다.
          </>
        }
        actions={
          write.allowed ? (
            <Link
              to="/policies/new"
              className="inline-flex items-center gap-1.5 rounded-lg bg-sky-700 px-3.5 py-2 text-sm font-medium text-white shadow-sm shadow-sky-700/25 hover:bg-sky-800"
            >
              새 정책
            </Link>
          ) : (
            <span className="text-xs text-slate-500" title={write.reason ?? undefined}>
              읽기 전용 계정입니다 — 정책을 만들거나 고칠 수 없습니다.
            </span>
          )
        }
      />

      <div className="grid gap-6 xl:grid-cols-5">
        <div className="xl:col-span-2">
          <Card
            title="정책 목록"
            description="행을 누르면 오른쪽에 상세와 실행 이력이 열립니다. 비활성화는 삭제가 아니며 비활성 정책도 목록에 남습니다."
          >
            {policiesQuery.isPending && <LoadingBlock />}
            {policiesQuery.isError && <ErrorBlock error={policiesQuery.error} />}
            {policiesQuery.data && policiesQuery.data.length === 0 && (
              <EmptyBlock>
                저장된 정책이 없습니다.{' '}
                {write.allowed && (
                  <Link to="/policies/new" className="text-sky-800 underline">
                    새 정책
                  </Link>
                )}
              </EmptyBlock>
            )}
            {allPolicies.length > 0 && (
              <ul className="divide-y divide-slate-100">
                {allPolicies.map((policy) => {
                  const selected = selectedId === policy.id;
                  return (
                    <li key={policy.id} className="py-2 first:pt-0 last:pb-0">
                      <div
                        className={cx(
                          'flex items-start justify-between gap-3 rounded-lg px-2.5 py-2 transition-colors',
                          selected
                            ? 'bg-sky-50 ring-1 ring-sky-200 ring-inset'
                            : 'hover:bg-slate-50',
                          !policy.active && 'opacity-80',
                        )}
                      >
                        <button
                          type="button"
                          aria-pressed={selected}
                          className="min-w-0 flex-1 cursor-pointer text-left"
                          onClick={() => setSelectedId(policy.id)}
                        >
                          <p className="flex flex-wrap items-center gap-2 font-medium text-slate-900">
                            {policy.name}
                            {!policy.active && <Badge tone="neutral">비활성</Badge>}
                            {!policy.allow_ai_analysis && <Badge tone="warning">AI 분석 불가</Badge>}
                            <ScheduleBadge {...policySchedule(policy)} />
                          </p>
                          <p
                            className="mt-1 truncate font-mono text-xs text-slate-500"
                            title={policy.logql}
                          >
                            {truncate(policy.logql, 70)}
                          </p>
                          <p className="mt-1 text-xs text-slate-500">
                            {policy.default_range_minutes}분 · 최대{' '}
                            {formatNumber(policy.max_lines)} 라인 · 대표 로그{' '}
                            {policy.max_samples_per_group}개 ·{' '}
                            {policy.daily_analysis_limit === null
                              ? '전역 한도'
                              : `일 ${policy.daily_analysis_limit}회`}
                          </p>
                          <p className="mt-1 text-xs text-slate-400">
                            수정 {formatDateTime(policy.updated_at)}
                          </p>
                        </button>

                        {/* 쓰기 동작 묶음 — viewer 에게는 자리 자체를 감춘다. */}
                        {write.allowed && (
                          <div className="flex shrink-0 flex-col gap-1">
                            <Link
                              to={`/policies/${policy.id}/edit`}
                              className="inline-flex items-center justify-center rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-800 hover:bg-slate-50"
                            >
                              수정
                            </Link>
                            {policy.active ? (
                              <Button
                                size="sm"
                                variant="danger"
                                disabled={deactivatePolicy.isPending}
                                onClick={() => deactivatePolicy.mutate(policy.id)}
                              >
                                비활성화
                              </Button>
                            ) : (
                              <Button
                                size="sm"
                                disabled={updatePolicy.isPending}
                                title="비활성화는 삭제가 아닙니다 — 다시 켤 수 있습니다."
                                onClick={() =>
                                  updatePolicy.mutate({ id: policy.id, payload: { active: true } })
                                }
                              >
                                재활성화
                              </Button>
                            )}
                          </div>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
        </div>

        <div className="xl:col-span-3">
          {selectedPolicy ? (
            <PolicyDetailCard policy={selectedPolicy} canEdit={write.allowed} />
          ) : (
            <Card title="정책 상세">
              <EmptyBlock>왼쪽 목록에서 정책을 고르십시오.</EmptyBlock>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------- 선택한 정책 상세 · 실행 이력

/** 조회 전용 상세. 여기서 값을 바꾸지 않는다 — 수정은 전용 페이지로 나간다. */
function PolicyDetailCard({ policy, canEdit }: { policy: PolicyRead; canEdit: boolean }) {
  const navigate = useNavigate();
  const runsQuery = usePolicyQueryRuns(policy.id);
  const schedule = policySchedule(policy);

  return (
    <div className="space-y-6">
      <Card
        title={
          <span className="flex flex-wrap items-center gap-2">
            {policy.name}
            <span className="text-xs font-normal text-slate-400">#{policy.id}</span>
            {!policy.active && <Badge tone="neutral">비활성</Badge>}
            {!policy.allow_ai_analysis && <Badge tone="warning">AI 분석 불가</Badge>}
            <ScheduleBadge {...schedule} />
          </span>
        }
        description={policy.description ?? '설명이 없습니다.'}
        actions={
          <div className="flex flex-wrap gap-2">
            <Link
              to={`/dashboard/${policy.id}`}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-800 hover:bg-slate-50"
            >
              <PlayIcon className="size-3.5" />
              대시보드에서 실행
            </Link>
            {canEdit && (
              <Link
                to={`/policies/${policy.id}/edit`}
                className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-800 hover:bg-slate-50"
              >
                이 정책 수정
              </Link>
            )}
          </div>
        }
      >
        <p className="text-xs font-medium text-slate-500">LogQL</p>
        <pre className="aila-scroll mt-1 overflow-x-auto rounded-lg bg-slate-900 px-3 py-2 text-xs whitespace-pre text-slate-100">
          {policy.logql}
        </pre>
        {/*
          분모 쿼리는 선택 항목이라 없는 정책이 더 많다 — 없을 때도 자리를 남겨 "설정할 수
          있는 값"임을 보이고, 미설정을 0 이 아니라 문장으로 적는다.
        */}
        <p className="mt-3 text-xs font-medium text-slate-500">분모 쿼리 (유입량 기준)</p>
        {policy.baseline_query ? (
          <pre className="aila-scroll mt-1 overflow-x-auto rounded-lg bg-slate-800 px-3 py-2 text-xs whitespace-pre text-slate-100">
            {policy.baseline_query}
          </pre>
        ) : (
          <p className="mt-1 text-xs text-slate-500">
            미설정 — 대시보드가 유입량·오류 비율을 계산하지 않습니다.
          </p>
        )}

        <dl className="mt-3 grid gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2">
          <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
            <dt className="text-slate-500">기본·상한 기간</dt>
            <dd className="text-slate-800">{policy.default_range_minutes}분</dd>
          </div>
          <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
            <dt className="text-slate-500">최대 조회 라인</dt>
            <dd className="text-slate-800">{formatNumber(policy.max_lines)}</dd>
          </div>
          <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
            <dt className="text-slate-500">그룹당 대표 로그</dt>
            <dd className="text-slate-800">{policy.max_samples_per_group}개</dd>
          </div>
          <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
            <dt className="text-slate-500">일일 분석 한도</dt>
            <dd className="text-slate-800">
              {policy.daily_analysis_limit === null
                ? '전역 한도만 적용'
                : `${policy.daily_analysis_limit}회`}
            </dd>
          </div>
          <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
            <dt className="text-slate-500">스케줄</dt>
            <dd className="text-slate-800">
              {schedule.enabled
                ? `${formatIntervalMinutes(schedule.intervalMinutes)}마다 조회`
                : '수동 실행만'}
            </dd>
          </div>
          <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
            <dt className="text-slate-500">신규 그룹 자동 분석</dt>
            <dd className="text-slate-800">
              {schedule.enabled && schedule.autoAnalyze ? (
                <span title={AUTO_ANALYZE_COST_NOTE}>켜짐 (처음 보는 오류·한도 내)</span>
              ) : (
                '꺼짐'
              )}
            </dd>
          </div>
          <div className="flex justify-between gap-3 border-b border-slate-100 py-1 sm:col-span-2">
            <dt className="text-slate-500">제외 정규식</dt>
            <dd className="text-right font-mono text-slate-800">
              {policy.exclusions.length === 0 ? '없음' : policy.exclusions.join(', ')}
            </dd>
          </div>
        </dl>
      </Card>

      <Card
        title="실행 이력"
        description="최신순 · 행을 누르면 그 회차의 오류 그룹으로 이동합니다."
      >
        {runsQuery.isPending && <LoadingBlock label="실행 이력을 불러오는 중…" />}

        {/* 백엔드에 아직 이 경로가 없을 수 있다 — 실패가 아니라 안내로 표시한다. */}
        {runsQuery.isError &&
          (isEndpointMissing(runsQuery.error) ? (
            <Notice tone="warning" title="실행 이력 API 를 아직 쓸 수 없습니다">
              <code className="rounded bg-white/60 px-1">
                GET /api/policies/{policy.id}/query-runs
              </code>{' '}
              가 응답하지 않습니다. 백엔드에 이 경로가 올라오면 여기에 회차별 조회 결과가
              표시됩니다.
            </Notice>
          ) : (
            <ErrorBlock error={runsQuery.error} />
          ))}

        {runsQuery.data && runsQuery.data.items.length === 0 && (
          <EmptyBlock>
            이 정책으로 실행한 조회가 없습니다. 대시보드에서 <strong>정책 실행</strong>을
            누르십시오.
          </EmptyBlock>
        )}

        {runsQuery.data && runsQuery.data.items.length > 0 && (
          <>
            <TableWrap minWidth="34rem">
              <thead>
                <tr>
                  <Th className="whitespace-nowrap">실행 시각</Th>
                  <Th className="whitespace-nowrap">실행 주체</Th>
                  <Th>상태</Th>
                  <Th align="right" className="whitespace-nowrap">
                    조회 / 제외
                  </Th>
                  <Th align="right" className="whitespace-nowrap">
                    그룹
                  </Th>
                  <Th>경고</Th>
                </tr>
              </thead>
              <tbody>
                {runsQuery.data.items.map((run) => (
                  <tr
                    key={run.id}
                    tabIndex={0}
                    role="link"
                    className="cursor-pointer hover:bg-sky-50 focus:bg-sky-50 focus:outline-none"
                    onClick={() => navigate(`/query-runs/${run.id}`)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        navigate(`/query-runs/${run.id}`);
                      }
                    }}
                  >
                    <Td className="whitespace-nowrap">
                      <p className="font-medium text-sky-800">{formatDateTime(run.started_at)}</p>
                      <p className="mt-0.5 text-xs text-slate-400">
                        #{run.id} · {formatRelative(run.started_at)}
                      </p>
                    </Td>
                    <Td>
                      {/* 값이 없으면(백엔드 미배포) 배지 대신 `-` 다 — 수동으로 채우지 않는다. */}
                      <TriggeredByBadge value={run.triggered_by} />
                      {!run.triggered_by && <span className="text-xs text-slate-400">-</span>}
                    </Td>
                    <Td>
                      <QueryRunStatusBadge status={run.status} />
                      {run.error_message && (
                        <p className="mt-1 max-w-64 text-xs break-words text-rose-700">
                          {truncate(run.error_message, 120)}
                        </p>
                      )}
                    </Td>
                    <Td align="right" className="whitespace-nowrap">
                      <span className="font-semibold text-slate-900">
                        {formatNumber(run.fetched_count)}
                      </span>
                      <span className="text-slate-400"> / </span>
                      <span className={run.dropped_count > 0 ? 'text-amber-700' : undefined}>
                        {formatNumber(run.dropped_count)}
                      </span>
                    </Td>
                    <Td align="right">{formatNumber(run.group_count)}</Td>
                    <Td>
                      {run.warnings.length === 0 ? (
                        <span className="text-xs text-slate-400">-</span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {run.warnings.map((warning, index) => (
                            <Badge
                              key={`${warning.code}-${index}`}
                              tone="warning"
                              title={warning.message}
                            >
                              {warningCodeLabel(warning.code)}
                              {warning.count != null ? ` (${formatNumber(warning.count)})` : ''}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>
            <p className="mt-2 text-xs text-slate-500">
              조회 수는 로그 라인 수이고 대시보드의 건수(metric)와 같은 값이 아닙니다.{' '}
              {runsQuery.data.total > runsQuery.data.items.length && (
                <>
                  전체 {formatNumber(runsQuery.data.total)}회 중 최근{' '}
                  {runsQuery.data.items.length}회만 표시합니다.
                </>
              )}
            </p>
          </>
        )}
      </Card>
    </div>
  );
}
