import { useMemo, useState } from 'react';
import { Link } from 'react-router';

import { useAnalysisJobs, useUsage } from '../api/queries';
import type { UsageParams } from '../api/types';
import { TokenByModelChart } from '../components/chartsLazy';
import { AnalysisStatusBadge, SeverityBadge } from '../components/StatusBadges';
import {
  Badge,
  Card,
  EmptyBlock,
  ErrorBlock,
  Field,
  LoadingBlock,
  PageHeader,
  Select,
  Stat,
  TableWrap,
  Td,
  Th,
} from '../components/ui';
import {
  formatDateTime,
  formatDuration,
  formatEstimatedCost,
  formatNumber,
  formatTokens,
  providerLabel,
} from '../lib/format';

const RANGES = [
  { days: 1, label: '최근 24시간' },
  { days: 7, label: '최근 7일' },
  { days: 30, label: '최근 30일' },
];

export function UsagePage() {
  const [rangeIndex, setRangeIndex] = useState(1);

  const params = useMemo<UsageParams>(() => {
    const end = new Date();
    const start = new Date(end.getTime() - RANGES[rangeIndex].days * 24 * 60 * 60_000);
    return { range_start: start.toISOString(), range_end: end.toISOString() };
  }, [rangeIndex]);

  const usageQuery = useUsage(params);
  const jobsQuery = useAnalysisJobs();

  const totalFailures = (usageQuery.data?.items ?? []).reduce(
    (acc, item) => acc + item.failure_count,
    0,
  );

  return (
    <div>
      <PageHeader
        title="분석 이력·사용량"
        description={
          <>
            비용은 계산 시점 단가표 기준 <strong>추정</strong>값입니다 — 캐시 적중·배치 할인에 따라
            실제 청구액과 벌어지므로 정산 근거로 쓰지 마십시오. 비용 차단은 이 화면이 아니라 일일
            분석 한도가 담당합니다.
          </>
        }
        actions={
          <Field label="기간" className="w-44">
            <Select
              value={rangeIndex}
              onChange={(event) => setRangeIndex(Number(event.target.value))}
            >
              {RANGES.map((range, index) => (
                <option key={range.label} value={index}>
                  {range.label}
                </option>
              ))}
            </Select>
          </Field>
        }
      />

      {usageQuery.isPending && <LoadingBlock />}
      {usageQuery.isError && <ErrorBlock error={usageQuery.error} />}

      {usageQuery.data && (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="총 추정 비용"
              value={formatEstimatedCost(usageQuery.data.total_estimated_cost)}
              sub="추정 — 정산 근거 아님"
              tone="accent"
            />
            <Stat
              label="분석 실행"
              value={formatNumber(usageQuery.data.total_jobs)}
              sub={`실패 ${formatNumber(totalFailures)}건`}
            />
            <Stat
              label="입력 토큰"
              value={formatNumber(usageQuery.data.total_input_tokens)}
              sub="프롬프트"
            />
            <Stat
              label="출력 토큰"
              value={formatNumber(usageQuery.data.total_output_tokens)}
              sub="구조화 응답"
            />
          </div>

          <Card
            title="모델별 토큰"
            description={`${formatDateTime(usageQuery.data.range_start)} ~ ${formatDateTime(
              usageQuery.data.range_end,
            )}`}
          >
            <TokenByModelChart items={usageQuery.data.items} />
          </Card>

          <Card title="모델별 집계" description="평균 응답 시간은 성공·실패를 모두 포함합니다.">
            {usageQuery.data.items.length === 0 ? (
              <EmptyBlock>이 기간에 기록된 사용량이 없습니다.</EmptyBlock>
            ) : (
              <TableWrap>
                <thead>
                  <tr>
                    <Th>프로바이더 · 모델</Th>
                    <Th align="right">실행</Th>
                    <Th align="right">실패</Th>
                    <Th align="right">입력 토큰</Th>
                    <Th align="right">출력 토큰</Th>
                    <Th align="right">추정 비용</Th>
                    <Th align="right">평균 응답</Th>
                  </tr>
                </thead>
                <tbody>
                  {usageQuery.data.items.map((item) => (
                    <tr key={`${item.provider}-${item.model}`} className="hover:bg-slate-50">
                      <Td>
                        <p className="font-medium text-slate-900">{providerLabel(item.provider)}</p>
                        <p className="mt-0.5 font-mono text-xs text-slate-500">{item.model}</p>
                      </Td>
                      <Td align="right">{formatNumber(item.job_count)}</Td>
                      <Td align="right">
                        {item.failure_count > 0 ? (
                          <Badge tone="danger">{formatNumber(item.failure_count)}</Badge>
                        ) : (
                          <span className="text-slate-400">0</span>
                        )}
                      </Td>
                      <Td align="right">{formatTokens(item.input_tokens)}</Td>
                      <Td align="right">{formatTokens(item.output_tokens)}</Td>
                      <Td align="right">
                        {formatEstimatedCost(item.estimated_cost)}
                        <span className="ml-1 text-xs text-slate-400">(추정)</span>
                      </Td>
                      <Td align="right">{formatDuration(item.avg_latency_ms)}</Td>
                    </tr>
                  ))}
                </tbody>
              </TableWrap>
            )}
          </Card>
        </div>
      )}

      <div className="mt-6">
        <Card
          title="분석 실행 목록"
          description="같은 오류를 다시 분석하기 전에 fingerprint 기준 이력을 먼저 확인하십시오."
        >
          {jobsQuery.isPending && <LoadingBlock />}
          {jobsQuery.isError && <ErrorBlock error={jobsQuery.error} />}
          {jobsQuery.data && jobsQuery.data.length === 0 && (
            <EmptyBlock>실행된 분석이 없습니다.</EmptyBlock>
          )}
          {jobsQuery.data && jobsQuery.data.length > 0 && (
            <TableWrap>
              <thead>
                <tr>
                  <Th>요청 시각</Th>
                  <Th>오류 그룹 · fingerprint</Th>
                  <Th>서비스 · 오류</Th>
                  <Th>모델</Th>
                  <Th>상태</Th>
                  <Th>요약</Th>
                </tr>
              </thead>
              <tbody>
                {jobsQuery.data.map((job) => (
                  <tr key={job.id} className="hover:bg-slate-50">
                    <Td>
                      <p>{formatDateTime(job.requested_at)}</p>
                      <p className="mt-0.5 text-xs text-slate-400">작업 #{job.id}</p>
                    </Td>
                    <Td>
                      <Link
                        to={`/error-groups/${job.error_group_id}`}
                        className="font-medium text-sky-800 hover:underline"
                      >
                        그룹 #{job.error_group_id}
                      </Link>
                      <p className="mt-0.5 font-mono text-xs text-slate-500">{job.fingerprint}</p>
                    </Td>
                    <Td>
                      <p>{job.service ?? '-'}</p>
                      <p className="mt-0.5 font-mono text-xs text-slate-500">
                        {job.error_type ?? '-'}
                        {job.environment ? ` · ${job.environment}` : ''}
                      </p>
                    </Td>
                    <Td>
                      <p>{providerLabel(job.provider)}</p>
                      <p className="mt-0.5 font-mono text-xs text-slate-500">{job.model}</p>
                    </Td>
                    <Td>
                      <div className="flex flex-col items-start gap-1">
                        <AnalysisStatusBadge status={job.status} />
                        {job.severity && <SeverityBadge severity={job.severity} />}
                        {job.status === 'failed' && job.error_message && (
                          <span className="text-xs text-rose-700">{job.error_message}</span>
                        )}
                      </div>
                    </Td>
                    <Td>
                      {/* 목록 응답에는 토큰·비용이 없다 — 위쪽 모델별 집계에서 본다. */}
                      <p className="max-w-md text-xs text-slate-600">
                        {job.summary ?? job.normalized_message ?? '-'}
                      </p>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>
          )}
        </Card>
      </div>
    </div>
  );
}
