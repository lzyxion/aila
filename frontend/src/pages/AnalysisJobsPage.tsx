/**
 * 분석 이력 (`/admin/analysis-jobs`) — 계약 2.
 *
 * `GET /api/analysis-jobs` 의 **추가 파라미터**(`q`·`requested_from`·`requested_to`)를 쓰는
 * 화면이다. 추가는 additive 라 모르는 백엔드는 파라미터를 무시하고 전체를 준다 — 그래서
 * 검색이 먹지 않는 상태는 오류가 아니라 "필터가 서버에 아직 없다"이고, 화면은 봉투가 같은
 * 목록을 그대로 그린다.
 *
 * 검색은 **입력할 때마다 보내지 않는다.** 이력 조회는 DB 를 훑는 요청이라 한 글자마다
 * 두드리면 서버가 아니라 사용자가 먼저 느려진다 — 엔터·버튼에서만 적용한다.
 *
 * 표시 규칙(계약): 목록 응답에는 토큰·비용이 없다(`result`·`usage` 없음). 그 값은
 * **사용량** 탭의 집계에서 본다 — 여기서 지어내지 않는다.
 */

import { useMemo, useState } from 'react';

import { useAnalysisJobs } from '../api/queries';
import type { AnalysisJobListParams, AnalysisJobStatus } from '../api/types';
import {
  BackIcon,
  ChevronRightIcon,
  EmptyIcon,
  SearchIcon,
} from '../components/icons';
import {
  AnalysisStatusBadge,
  SeverityBadge,
  TriggeredByBadge,
} from '../components/StatusBadges';
import {
  Button,
  Card,
  Code,
  EmptyBlock,
  ErrorBlock,
  Field,
  Input,
  PageStack,
  Select,
  SkeletonTable,
  TableWrap,
  Td,
  Th,
  TextLink,
} from '../components/ui';
import {
  formatDateTime,
  formatNumber,
  fromLocalInputValue,
  providerLabel,
  toLocalInputValue,
} from '../lib/format';

const STATUSES: Array<{ value: '' | AnalysisJobStatus; label: string }> = [
  { value: '', label: '전체 상태' },
  { value: 'pending', label: '대기 중' },
  { value: 'running', label: '분석 중' },
  { value: 'succeeded', label: '분석 완료' },
  { value: 'failed', label: '분석 실패' },
];

const PAGE_SIZES = [20, 50, 100];

/** 기간 프리셋. 값은 즉시 from/to 입력에 채워지고, 적용은 검색 버튼이 한다. */
const PRESETS: Array<{ label: string; days: number | null }> = [
  { label: '전체 기간', days: null },
  { label: '최근 24시간', days: 1 },
  { label: '최근 7일', days: 7 },
  { label: '최근 30일', days: 30 },
];

interface Draft {
  q: string;
  status: '' | AnalysisJobStatus;
  /** `datetime-local` 입력값 (로컬 시각). 전송 시 ISO 로 바꾼다. */
  from: string;
  to: string;
}

const EMPTY_DRAFT: Draft = { q: '', status: '', from: '', to: '' };

export function AnalysisJobsPage() {
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  /** 실제로 서버에 보낸 필터. 입력(`draft`)과 분리해야 타이핑이 조회를 유발하지 않는다. */
  const [applied, setApplied] = useState<Draft>(EMPTY_DRAFT);
  const [limit, setLimit] = useState(20);
  const [offset, setOffset] = useState(0);

  const params = useMemo<AnalysisJobListParams>(
    () => ({
      ...(applied.status ? { status: applied.status } : {}),
      ...(applied.q.trim() ? { q: applied.q.trim() } : {}),
      ...(applied.from ? { requested_from: fromLocalInputValue(applied.from) } : {}),
      ...(applied.to ? { requested_to: fromLocalInputValue(applied.to) } : {}),
      limit,
      offset,
    }),
    [applied, limit, offset],
  );

  const jobsQuery = useAnalysisJobs(params);
  const data = jobsQuery.data;
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const hasFilter = Boolean(applied.q.trim() || applied.status || applied.from || applied.to);

  function apply(next: Draft) {
    setDraft(next);
    setApplied(next);
    // 필터가 바뀌면 첫 페이지로 — 그러지 않으면 3페이지에서 검색해 빈 화면을 본다.
    setOffset(0);
  }

  function applyPreset(days: number | null) {
    const next: Draft =
      days === null
        ? { ...draft, from: '', to: '' }
        : {
            ...draft,
            from: toLocalInputValue(
              new Date(Date.now() - days * 24 * 60 * 60_000).toISOString(),
            ),
            to: toLocalInputValue(new Date().toISOString()),
          };
    apply(next);
  }

  return (
    <PageStack>
      <Card
        title="분석 이력 검색"
        description="같은 오류를 다시 분석하기 전에 fingerprint 기준 이력을 먼저 확인하십시오."
        info={
          <>
            검색어는 <strong>서비스·모델·정규화 메시지·fingerprint</strong> 에 부분 일치합니다.
            <span className="mt-1.5 block">
              걸러 주는 쪽은 <strong>서버</strong>입니다. 백엔드가 이 파라미터를 아직 모르면
              필터 없이 전체를 돌려줍니다 — 추가 파라미터라 오류가 아니며, 결과가 좁혀지지
              않으면 그 경로가 배포됐는지 확인하십시오.
            </span>
          </>
        }
        actions={
          hasFilter && (
            <Button size="sm" variant="ghost" onClick={() => apply(EMPTY_DRAFT)}>
              필터 초기화
            </Button>
          )
        }
      >
        <form
          className="grid items-end gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1.2fr)_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            apply(draft);
          }}
        >
          <Field label="검색어" hint="서비스 · 모델 · 정규화 메시지 · fingerprint">
            <Input
              value={draft.q}
              placeholder="payment-api, TimeoutError, fp_9c1a…"
              onChange={(event) => setDraft({ ...draft, q: event.target.value })}
            />
          </Field>

          <Field label="상태">
            <Select
              value={draft.status}
              onChange={(event) => {
                // 상태는 값이 하나뿐이라 고르는 즉시 적용하는 편이 자연스럽다.
                apply({ ...draft, status: event.target.value as Draft['status'] });
              }}
            >
              {STATUSES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="요청 시각 · 시작">
            <Input
              type="datetime-local"
              value={draft.from}
              onChange={(event) => setDraft({ ...draft, from: event.target.value })}
            />
          </Field>

          <Field label="요청 시각 · 끝">
            <Input
              type="datetime-local"
              value={draft.to}
              onChange={(event) => setDraft({ ...draft, to: event.target.value })}
            />
          </Field>

          <Button type="submit" variant="primary" className="mb-0.5">
            <SearchIcon aria-hidden className="size-4" />
            검색
          </Button>
        </form>

        {/*
          걸러 주는 쪽은 서버다. 화면에서 한 페이지만 다시 거르면 `total` 과 페이지네이션이
          어긋나고 다음 페이지의 일치 항목이 영원히 보이지 않는다. (그 사실은 카드 머리말의
          ⓘ 로 옮겼다 — 지운 것이 아니다.)
        */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-muted">기간 프리셋</span>
          {PRESETS.map((preset) => (
            <Button key={preset.label} size="sm" onClick={() => applyPreset(preset.days)}>
              {preset.label}
            </Button>
          ))}
        </div>
      </Card>

      <Card
        title="분석 실행 목록"
        description="토큰·추정 비용은 목록 응답에 없습니다 — 사용량 탭의 집계에서 봅니다."
        info={
          <>
            목록 응답에는 <Code>result</Code>·<Code>usage</Code> 가 없습니다. 여기 토큰·비용을
            적으면 지어내는 것이 되므로 <strong>사용량</strong> 탭의 집계에서 봅니다.
            <span className="mt-1.5 block">
              정렬은 <strong>최신순</strong>이고, 같은 초에 들어간 두 건은 작업 id 로 순서가
              고정됩니다.
            </span>
          </>
        }
        actions={
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted">
              {hasFilter ? '조건에 맞는 ' : '전체 '}
              <strong className="text-ink-soft tabular-nums">{formatNumber(total)}</strong>건
              {total > 0 && ` 중 ${offset + 1}–${Math.min(offset + limit, total)}`}
            </span>
            {/* 폭은 감싸는 div 가 정한다 — Select 자신이 `w-full` 을 들고 있다. */}
            <div className="w-28">
              <Select
                className="py-1.5 text-xs"
                value={limit}
                aria-label="페이지당 건수"
                onChange={(event) => {
                  setLimit(Number(event.target.value));
                  setOffset(0);
                }}
              >
                {PAGE_SIZES.map((size) => (
                  <option key={size} value={size}>
                    {size}건씩
                  </option>
                ))}
              </Select>
            </div>
          </div>
        }
      >
        {/* 표의 뼈대(열 7개)는 이미 정해져 있다 — 그 자리를 스켈레톤으로 채워 두면
            결과가 도착할 때 레이아웃이 튀지 않는다. 행 수는 요청한 페이지 크기다. */}
        {jobsQuery.isPending && <SkeletonTable rows={Math.min(limit, 8)} cols={7} label="분석 이력을 불러오는 중" />}
        {jobsQuery.isError && <ErrorBlock error={jobsQuery.error} />}

        {data && items.length === 0 && (
          <EmptyBlock icon={EmptyIcon}>
            {hasFilter
              ? '조건에 맞는 분석 실행이 없습니다. 검색어나 기간을 넓혀 보십시오.'
              : '실행된 분석이 없습니다.'}
          </EmptyBlock>
        )}

        {items.length > 0 && (
          <>
            <TableWrap minWidth="64rem">
              <thead>
                <tr>
                  <Th>요청 시각</Th>
                  <Th>실행 주체</Th>
                  <Th>오류 그룹 · fingerprint</Th>
                  <Th>서비스 · 오류</Th>
                  <Th>모델</Th>
                  <Th>상태</Th>
                  <Th>요약</Th>
                </tr>
              </thead>
              <tbody>
                {items.map((job) => (
                  <tr key={job.id} className="hover:bg-surface-2">
                    <Td className="whitespace-nowrap">
                      <p className="tabular-nums">{formatDateTime(job.requested_at)}</p>
                      <p className="mt-0.5 text-xs text-faint tabular-nums">작업 #{job.id}</p>
                    </Td>
                    <Td>
                      <TriggeredByBadge value={job.triggered_by} />
                      {!job.triggered_by && <span className="text-xs text-faint">-</span>}
                    </Td>
                    <Td>
                      <TextLink to={`/error-groups/${job.error_group_id}`}>
                        그룹 #{job.error_group_id}
                      </TextLink>
                      <p className="mt-0.5 font-mono text-xs text-muted">{job.fingerprint}</p>
                    </Td>
                    <Td>
                      <p>{job.service ?? '-'}</p>
                      <p className="mt-0.5 font-mono text-xs text-muted">
                        {job.error_type ?? '-'}
                        {job.environment ? ` · ${job.environment}` : ''}
                      </p>
                    </Td>
                    <Td>
                      <p>{providerLabel(job.provider)}</p>
                      <p className="mt-0.5 font-mono text-xs text-muted">{job.model}</p>
                    </Td>
                    <Td>
                      <div className="flex flex-col items-start gap-1">
                        <AnalysisStatusBadge status={job.status} />
                        {job.severity && <SeverityBadge severity={job.severity} />}
                        {job.status === 'failed' && job.error_message && (
                          <span className="text-xs text-rose-700 dark:text-rose-300">
                            {job.error_message}
                          </span>
                        )}
                      </div>
                    </Td>
                    <Td>
                      <p className="max-w-md text-xs text-muted">
                        {job.summary ?? job.normalized_message ?? '-'}
                      </p>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>

            <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
              <Button
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - limit))}
              >
                <BackIcon aria-hidden className="size-3.5" />
                이전
              </Button>
              <span className="text-xs text-muted tabular-nums">
                {Math.floor(offset / limit) + 1} / {Math.max(1, Math.ceil(total / limit))}
              </span>
              <Button
                size="sm"
                disabled={offset + limit >= total}
                onClick={() => setOffset(offset + limit)}
              >
                다음
                <ChevronRightIcon aria-hidden className="size-3.5" />
              </Button>
            </div>
          </>
        )}
      </Card>
    </PageStack>
  );
}
