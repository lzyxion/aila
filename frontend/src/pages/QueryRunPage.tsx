/**
 * 한 조회 회차(query run)의 오류 그룹 목록.
 *
 * 정책 화면의 실행 이력에서 회차를 누르면 여기로 온다 — "이 정책이 그때 무엇을 잡았나"를
 * 회차 단위로 되짚는 경로다. 대시보드는 기간 기준 상위 그룹만 보여주므로 회차 전체를
 * 보려면 이 화면이 필요하다.
 *
 * 표시 규칙(계약): 건수는 그룹화된 라인 수이고, 분석 상태는 그룹 id 가 아니라
 * **fingerprint 기준**이다 — 이전 회차에서 분석한 오류도 "분석 완료"로 보인다.
 */

import { Link, useParams } from 'react-router';

import { useErrorGroups, useQueryRun } from '../api/queries';
import {
  AnalysisStatusBadge,
  QueryRunStatusBadge,
  SeverityBadge,
  TriggeredByBadge,
} from '../components/StatusBadges';
import {
  Badge,
  Card,
  EmptyBlock,
  ErrorBlock,
  LoadingBlock,
  PageHeader,
  Stat,
  TableWrap,
  Td,
  Th,
} from '../components/ui';
import { formatDateTime, formatNumber, formatRelative, truncate, warningCodeLabel } from '../lib/format';

export function QueryRunPage() {
  const params = useParams<{ runId: string }>();
  const runId = params.runId ? Number(params.runId) : null;

  const runQuery = useQueryRun(runId);
  const groupsQuery = useErrorGroups(runId);

  if (runId === null || Number.isNaN(runId)) {
    return <ErrorBlock error="잘못된 조회 id 입니다." />;
  }

  const run = runQuery.data;

  return (
    <div>
      <PageHeader
        title={`조회 #${runId}`}
        description={
          run ? (
            <>
              {formatDateTime(run.range_start)} ~ {formatDateTime(run.range_end)} 구간을{' '}
              {formatDateTime(run.started_at)} 에 실행했습니다. 아래 그룹의 분석 상태는 그룹 id 가
              아니라 <strong>fingerprint 기준</strong>입니다.
            </>
          ) : (
            '이 회차가 묶은 오류 그룹입니다.'
          )
        }
        actions={
          <Link
            to="/policies"
            className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50"
          >
            정책 목록으로
          </Link>
        }
      />

      {runQuery.isPending && <LoadingBlock />}
      {runQuery.isError && <ErrorBlock error={runQuery.error} />}

      {run && (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="조회 라인"
              value={formatNumber(run.fetched_count)}
              sub="metric 건수가 아니라 실제 조회 라인 수"
              tone="accent"
            />
            <Stat
              label="제외 라인"
              value={formatNumber(run.dropped_count)}
              sub="제외 정규식·파싱 실패"
            />
            <Stat label="오류 그룹" value={formatNumber(run.group_count)} sub="fingerprint 단위" />
            <Stat
              label="상태"
              value={
                <span className="flex flex-wrap items-center gap-1.5">
                  <QueryRunStatusBadge status={run.status} />
                  <TriggeredByBadge value={run.triggered_by} />
                </span>
              }
              sub={run.finished_at ? `종료 ${formatRelative(run.finished_at)}` : '진행 중'}
            />
          </div>

          {run.error_message && (
            <ErrorBlock error={run.error_message} hint="조회가 실패해 그룹이 만들어지지 않았습니다." />
          )}

          {run.warnings.length > 0 && (
            <Card title="조회 경고" description="조정·누락 사실은 경고 코드로 남습니다.">
              <ul className="space-y-1.5 text-sm">
                {run.warnings.map((warning, index) => (
                  <li key={`${warning.code}-${index}`} className="flex flex-wrap items-baseline gap-2">
                    <Badge tone="warning">{warningCodeLabel(warning.code)}</Badge>
                    <span className="text-slate-700">
                      {warning.message}
                      {warning.count != null && ` (${formatNumber(warning.count)}건)`}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <Card
            title="오류 그룹"
            description="유사 오류를 fingerprint 로 묶은 결과입니다. 그룹을 누르면 마스킹된 대표 로그와 AI 분석으로 들어갑니다."
          >
            {groupsQuery.isPending && <LoadingBlock />}
            {groupsQuery.isError && <ErrorBlock error={groupsQuery.error} />}
            {groupsQuery.data && groupsQuery.data.items.length === 0 && (
              <EmptyBlock>이 회차에서 묶인 오류 그룹이 없습니다.</EmptyBlock>
            )}
            {groupsQuery.data && groupsQuery.data.items.length > 0 && (
              <TableWrap minWidth="44rem">
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
                  {groupsQuery.data.items.map((group) => (
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
      )}
    </div>
  );
}
