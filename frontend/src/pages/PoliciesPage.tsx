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
 *
 * 밀도(Phase 8): 머리말은 한 줄로 줄이고 계약 문구("조회 수는 로그 라인 수이지 대시보드의
 * 건수가 아니다", "분모 쿼리가 없으면 0 이 아니라 -")는 **지우지 않고** ⓘ 로 옮겼다.
 */

import { useEffect, useState, type ReactNode } from 'react';
import { useNavigate, useSearchParams } from 'react-router';

import { isEndpointMissing } from '../api/client';
import {
  useDeactivatePolicy,
  usePolicies,
  usePolicyQueryRuns,
  useUpdatePolicy,
} from '../api/queries';
import { policySchedule, type PolicyRead } from '../api/types';
import { useWriteAccess } from '../auth/AuthContext';
import { AddIcon, EditIcon, EmptyIcon, PolicyIcon, RunIcon } from '../components/icons';
import {
  QueryRunStatusBadge,
  ScheduleBadge,
  TriggeredByBadge,
} from '../components/StatusBadges';
import {
  Badge,
  Button,
  ButtonLink,
  Card,
  Code,
  EmptyBlock,
  ErrorBlock,
  InfoTip,
  LoadingBlock,
  LogLine,
  Notice,
  PageHeader,
  PageStack,
  SkeletonTable,
  TableWrap,
  Td,
  TextLink,
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

/**
 * 조회 회차의 "조회 / 제외" 수가 무엇인지 — **계약 문구**다.
 *
 * 대시보드의 건수는 `count_over_time` metric 결과이고 여기 수는 실제로 받아 온 로그
 * 라인 수라, 두 화면의 숫자가 다를 때 "어느 쪽이 틀렸나"를 묻게 된다. 본문에서 ⓘ 로
 * 옮기되 문장은 그대로 둔다.
 */
const RUN_COUNT_NOTE = (
  <>
    여기 수는 실제로 <strong>받아 온 로그 라인 수</strong>입니다. 대시보드의 건수는{' '}
    <Code>count_over_time</Code> metric 결과라 같은 값이 아닙니다.
    <span className="mt-1.5 block">
      제외 수는 정책의 제외 정규식에 걸려 <strong>그룹화 전에 버려진</strong> 라인입니다.
    </span>
  </>
);

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
        description="정책은 LogQL 한 줄이 아니라 실행 한도를 포함한 묶음입니다."
        info={
          <>
            이 화면은 <strong>조회 전용</strong>입니다 — 추가·수정은 전용 페이지에서 하고,
            저장 전 미리보기도 거기 있습니다.
            <span className="mt-1.5 block">
              기간·라인 수는 정책의 기본값이자 <strong>실행 상한</strong>이라, 대시보드에서
              더 넓게 조회해도 서버가 이 값으로 자릅니다.
            </span>
          </>
        }
        actions={
          write.allowed ? (
            <ButtonLink to="/policies/new" variant="primary">
              <AddIcon aria-hidden className="size-4" />새 정책
            </ButtonLink>
          ) : (
            // 권한 안내는 ⓘ 로 숨기지 않는다 — 버튼이 없는 이유가 글자로 남아야 한다.
            <span
              className="flex flex-wrap items-center gap-1.5 text-xs text-muted"
              title={write.reason ?? undefined}
            >
              <Badge tone="neutral">viewer · 읽기 전용</Badge>
              정책을 만들거나 고칠 수 없습니다.
            </span>
          )
        }
      />

      <div className="grid gap-6 xl:grid-cols-5">
        <div className="xl:col-span-2">
          <Card
            title="정책 목록"
            description="행을 누르면 오른쪽에 상세와 실행 이력이 열립니다."
            info={
              <>
                <strong>비활성화는 삭제가 아닙니다</strong> — 비활성 정책도 목록에 남고 실행
                이력도 그대로 보존됩니다.
                <span className="mt-1.5 block">
                  비활성 정책은 대시보드의 정책 선택 목록에서만 빠집니다.
                </span>
              </>
            }
          >
            {/*
              여기는 스켈레톤을 쓰지 않는다 — 저장된 정책이 **0 개일 수 있는** 자리라
              행 모양을 미리 그리면 "몇 건 있다"는 거짓 신호가 된다 (설치 직후가 정확히 그렇다).
            */}
            {policiesQuery.isPending && <LoadingBlock label="정책 목록을 불러오는 중…" />}
            {policiesQuery.isError && <ErrorBlock error={policiesQuery.error} />}
            {policiesQuery.data && policiesQuery.data.length === 0 && (
              <EmptyBlock icon={PolicyIcon}>
                저장된 정책이 없습니다.{' '}
                {write.allowed && <TextLink to="/policies/new">새 정책 만들기</TextLink>}
              </EmptyBlock>
            )}
            {allPolicies.length > 0 && (
              <ul className="-mx-1.5 divide-y divide-line-soft">
                {allPolicies.map((policy) => {
                  const selected = selectedId === policy.id;
                  return (
                    <li key={policy.id} className="py-1.5 first:pt-0 last:pb-0">
                      <div
                        className={cx(
                          'flex items-start justify-between gap-2 rounded-lg px-2.5 py-2 transition-colors',
                          // 선택 표시는 배경만이 아니다 — 링과 `aria-pressed` 를 함께 둔다.
                          selected
                            ? 'bg-accent-soft ring-1 ring-line-strong ring-inset'
                            : 'hover:bg-surface-2',
                        )}
                      >
                        <button
                          type="button"
                          aria-pressed={selected}
                          className="min-w-0 flex-1 cursor-pointer rounded-md text-left"
                          onClick={() => setSelectedId(policy.id)}
                        >
                          <span className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-ink">
                            {policy.name}
                            <span className="text-xs font-normal text-faint tabular-nums">
                              #{policy.id}
                            </span>
                            {/* 비활성은 흐리게가 아니라 **글자**로 — 투명도는 대비만 깎는다. */}
                            {!policy.active && <Badge tone="neutral">비활성</Badge>}
                            {!policy.allow_ai_analysis && (
                              <Badge tone="warning" title="이 정책에서는 LLM 분석을 실행할 수 없습니다.">
                                AI 분석 불가
                              </Badge>
                            )}
                            <ScheduleBadge {...policySchedule(policy)} />
                          </span>
                          <span
                            className="mt-1 block truncate font-mono text-xs text-faint"
                            title={policy.logql}
                          >
                            {truncate(policy.logql, 70)}
                          </span>
                          <span className="mt-1 block text-xs text-muted tabular-nums">
                            {policy.default_range_minutes}분 · 최대{' '}
                            {formatNumber(policy.max_lines)} 라인 · 대표 로그{' '}
                            {policy.max_samples_per_group}개 ·{' '}
                            {policy.daily_analysis_limit === null
                              ? '전역 한도'
                              : `일 ${policy.daily_analysis_limit}회`}
                          </span>
                          <span
                            className="mt-1 block text-xs text-faint"
                            title={formatDateTime(policy.updated_at)}
                          >
                            수정 {formatRelative(policy.updated_at)}
                          </span>
                        </button>

                        {/* 쓰기 동작 묶음 — viewer 에게는 자리 자체를 감춘다. */}
                        {write.allowed && (
                          <div className="flex shrink-0 flex-col gap-1">
                            <ButtonLink to={`/policies/${policy.id}/edit`} size="sm">
                              <EditIcon aria-hidden className="size-3.5" />
                              수정
                            </ButtonLink>
                            {policy.active ? (
                              <Button
                                size="sm"
                                variant="danger"
                                disabled={deactivatePolicy.isPending}
                                title="삭제가 아닙니다 — 목록·실행 이력은 남고 다시 켤 수 있습니다."
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
              {/* 목록이 아직 오는 중이면 "왼쪽에서 고르십시오"는 거짓 안내다. */}
              {policiesQuery.isPending ? (
                <LoadingBlock label="정책을 불러오는 중…" />
              ) : (
                <EmptyBlock icon={PolicyIcon}>왼쪽 목록에서 정책을 고르십시오.</EmptyBlock>
              )}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------- 선택한 정책 상세 · 실행 이력

/** 상세 안의 소제목. 값이 스무 개 넘게 늘어서면 위계 없이는 읽히지 않는다. */
function SectionLabel({ children, info }: { children: string; info?: ReactNode }) {
  return (
    <p className="flex items-center gap-1 text-xs font-semibold tracking-wide text-muted uppercase">
      <span>{children}</span>
      {info && <InfoTip title={children}>{info}</InfoTip>}
    </p>
  );
}

/** 설정 값 한 줄. 라벨/값의 대비를 상세 전체에서 같게 맞춘다. */
function SettingRow({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx('flex justify-between gap-3 border-b border-line-soft py-1', className)}>
      <dt className="text-muted">{label}</dt>
      <dd className="text-right text-ink-soft">{children}</dd>
    </div>
  );
}

/** 조회 전용 상세. 여기서 값을 바꾸지 않는다 — 수정은 전용 페이지로 나간다. */
function PolicyDetailCard({ policy, canEdit }: { policy: PolicyRead; canEdit: boolean }) {
  const navigate = useNavigate();
  const runsQuery = usePolicyQueryRuns(policy.id);
  const schedule = policySchedule(policy);
  const autoAnalyzeOn = schedule.enabled && schedule.autoAnalyze;

  return (
    <PageStack>
      <Card
        title={
          <span className="flex flex-wrap items-center gap-2">
            {policy.name}
            <span className="text-xs font-normal text-faint tabular-nums">#{policy.id}</span>
            {!policy.active && <Badge tone="neutral">비활성</Badge>}
            {!policy.allow_ai_analysis && <Badge tone="warning">AI 분석 불가</Badge>}
            <ScheduleBadge {...schedule} />
          </span>
        }
        description={policy.description ?? '설명이 없습니다.'}
        actions={
          <>
            <ButtonLink to={`/dashboard/${policy.id}`} size="sm">
              <RunIcon aria-hidden className="size-3.5" />
              대시보드에서 실행
            </ButtonLink>
            {canEdit && (
              <ButtonLink to={`/policies/${policy.id}/edit`} size="sm">
                <EditIcon aria-hidden className="size-3.5" />이 정책 수정
              </ButtonLink>
            )}
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <SectionLabel>LogQL</SectionLabel>
            <div className="mt-1.5">
              <LogLine>{policy.logql}</LogLine>
            </div>
          </div>

          {/*
            분모 쿼리는 선택 항목이라 없는 정책이 더 많다 — 없을 때도 자리를 남겨 "설정할 수
            있는 값"임을 보이고, 미설정을 0 이 아니라 문장으로 적는다.
          */}
          <div>
            <SectionLabel
              info={
                <>
                  오류 셀렉터와 <strong>같은 라벨 범위</strong>의 전체 로그를 세는 쿼리 —
                  유입량·오류 비율의 <strong>분모</strong>입니다.
                  <span className="mt-1.5 block">
                    미설정이면 대시보드의 유입량·비율은 <strong>0 이 아니라</strong>{' '}
                    <Code>-</Code> 로 표시됩니다.
                  </span>
                </>
              }
            >
              분모 쿼리 (유입량 기준)
            </SectionLabel>
            {policy.baseline_query ? (
              <div className="mt-1.5">
                <LogLine>{policy.baseline_query}</LogLine>
              </div>
            ) : (
              <p className="mt-1.5 text-xs text-muted">
                미설정 — 대시보드가 유입량·오류 비율을 계산하지 않습니다.
              </p>
            )}
          </div>

          <div>
            <SectionLabel>조회 한도</SectionLabel>
            <dl className="mt-1.5 grid gap-x-6 text-xs sm:grid-cols-2">
              <SettingRow label="기본·상한 기간">
                <span className="tabular-nums">{policy.default_range_minutes}분</span>
              </SettingRow>
              <SettingRow label="최대 조회 라인">
                <span className="tabular-nums">{formatNumber(policy.max_lines)}</span>
              </SettingRow>
              <SettingRow label="그룹당 대표 로그">
                <span className="tabular-nums">{policy.max_samples_per_group}개</span>
              </SettingRow>
              <SettingRow label="제외 정규식" className="sm:col-span-2">
                <span className="font-mono break-all">
                  {policy.exclusions.length === 0 ? '없음' : policy.exclusions.join(', ')}
                </span>
              </SettingRow>
            </dl>
          </div>

          <div>
            <SectionLabel>분석 · 스케줄</SectionLabel>
            <dl className="mt-1.5 grid gap-x-6 text-xs sm:grid-cols-2">
              <SettingRow label="일일 분석 한도">
                {policy.daily_analysis_limit === null ? (
                  '전역 한도만 적용'
                ) : (
                  <span className="tabular-nums">{policy.daily_analysis_limit}회</span>
                )}
              </SettingRow>
              <SettingRow label="스케줄">
                {schedule.enabled
                  ? `${formatIntervalMinutes(schedule.intervalMinutes)}마다 조회`
                  : '수동 실행만'}
              </SettingRow>
              <SettingRow label="신규 그룹 자동 분석" className="sm:col-span-2">
                {autoAnalyzeOn ? (
                  <span className="inline-flex items-center gap-1">
                    켜짐 — 처음 보는 오류·한도 내
                    {/* 비용 경고는 배지로 본문에 남기고, 문장 전체는 ⓘ 안에 보존한다. */}
                    <InfoTip
                      label="자동 분석 비용 조건 보기"
                      title="비용이 나가는 자동 경로"
                      align="end"
                    >
                      {AUTO_ANALYZE_COST_NOTE}
                      <span className="mt-1.5 block">
                        이미 분석 이력이 있는 fingerprint 는 다시 돌지 않습니다 — 실패 이력도
                        "이력 있음"입니다.
                      </span>
                    </InfoTip>
                  </span>
                ) : (
                  '꺼짐'
                )}
              </SettingRow>
            </dl>
          </div>
        </div>
      </Card>

      <Card
        title="실행 이력"
        description="최신순 · 행을 누르면 그 회차의 오류 그룹으로 이동합니다."
      >
        {/* 표는 열이 고정된 자리라 스켈레톤이 레이아웃 이동을 없애 준다. */}
        {runsQuery.isPending && <SkeletonTable rows={4} cols={6} label="실행 이력을 불러오는 중" />}

        {/* 백엔드에 아직 이 경로가 없을 수 있다 — 실패가 아니라 안내로 표시한다. */}
        {runsQuery.isError &&
          (isEndpointMissing(runsQuery.error) ? (
            <Notice tone="warning" title="실행 이력 API 를 아직 쓸 수 없습니다">
              <Code>GET /api/policies/{policy.id}/query-runs</Code> 가 응답하지 않습니다. 백엔드에
              이 경로가 올라오면 여기에 회차별 조회 결과가 표시됩니다.
            </Notice>
          ) : (
            <ErrorBlock error={runsQuery.error} />
          ))}

        {runsQuery.data && runsQuery.data.items.length === 0 && (
          <EmptyBlock icon={EmptyIcon}>
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
                    <span className="inline-flex items-center gap-1">
                      조회 / 제외
                      <InfoTip label="조회·제외 수의 기준 보기" title="조회 / 제외" align="end">
                        {RUN_COUNT_NOTE}
                      </InfoTip>
                    </span>
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
                    // focus 링은 지우지 않는다 (전역 focus-visible 아웃라인이 붙는다).
                    className="cursor-pointer transition-colors hover:bg-surface-2 focus-visible:bg-surface-2"
                    onClick={() => navigate(`/query-runs/${run.id}`)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        navigate(`/query-runs/${run.id}`);
                      }
                    }}
                  >
                    <Td className="whitespace-nowrap">
                      <p className="font-medium text-accent-ink underline underline-offset-2">
                        {formatDateTime(run.started_at)}
                      </p>
                      <p className="mt-0.5 text-xs text-faint tabular-nums">
                        #{run.id} · {formatRelative(run.started_at)}
                      </p>
                    </Td>
                    <Td>
                      {/* 값이 없으면(백엔드 미배포) 배지 대신 `-` 다 — 수동으로 채우지 않는다. */}
                      <TriggeredByBadge value={run.triggered_by} />
                      {!run.triggered_by && <span className="text-xs text-faint">-</span>}
                    </Td>
                    <Td>
                      <QueryRunStatusBadge status={run.status} />
                      {run.error_message && (
                        <p className="mt-1 max-w-64 text-xs break-words text-rose-700 dark:text-rose-300">
                          {truncate(run.error_message, 120)}
                        </p>
                      )}
                    </Td>
                    <Td align="right" className="whitespace-nowrap">
                      <span className="font-semibold text-ink">
                        {formatNumber(run.fetched_count)}
                      </span>
                      <span className="text-faint"> / </span>
                      <span
                        className={
                          run.dropped_count > 0
                            ? 'font-medium text-amber-700 dark:text-amber-300'
                            : undefined
                        }
                      >
                        {formatNumber(run.dropped_count)}
                      </span>
                    </Td>
                    <Td align="right">{formatNumber(run.group_count)}</Td>
                    <Td>
                      {run.warnings.length === 0 ? (
                        <span className="text-xs text-faint">-</span>
                      ) : (
                        <div className="flex flex-wrap items-center gap-1.5">
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
            {runsQuery.data.total > runsQuery.data.items.length && (
              <p className="mt-2 text-xs text-muted tabular-nums">
                전체 {formatNumber(runsQuery.data.total)}회 중 최근{' '}
                {runsQuery.data.items.length}회만 표시합니다.
              </p>
            )}
          </>
        )}
      </Card>
    </PageStack>
  );
}
