/**
 * 한 조회 회차(query run)의 오류 그룹 목록.
 *
 * 정책 화면의 실행 이력에서 회차를 누르면 여기로 온다 — "이 정책이 그때 무엇을 잡았나"를
 * 회차 단위로 되짚는 경로다. 대시보드는 기간 기준 상위 그룹만 보여주므로 회차 전체를
 * 보려면 이 화면이 필요하다.
 *
 * 표시 규칙(계약): 건수는 그룹화된 라인 수이고, 분석 상태는 그룹 id 가 아니라
 * **fingerprint 기준**이다 — 이전 회차에서 분석한 오류도 "분석 완료"로 보인다.
 * (Phase 8: 이 문구들은 지우지 않고 ⓘ 로 옮겼다.)
 */

import { useParams } from 'react-router';

import { useErrorGroups, useQueryRun } from '../api/queries';
import { ErrorGroupIcon, GroupCountIcon, PolicyIcon } from '../components/icons';
import {
  AnalysisStatusBadge,
  QueryRunStatusBadge,
  SeverityBadge,
  TriggeredByBadge,
} from '../components/StatusBadges';
import {
  Badge,
  ButtonLink,
  Card,
  Code,
  EmptyBlock,
  ErrorBlock,
  LoadingBlock,
  PageHeader,
  PageStack,
  SkeletonStats,
  Stat,
  TableWrap,
  Td,
  TextLink,
  Th,
} from '../components/ui';
import { formatDateTime, formatNumber, formatRelative, truncate, warningCodeLabel } from '../lib/format';

const FINGERPRINT_NOTE = (
  <>
    분석 상태는 그룹 id 가 아니라 <strong>fingerprint 기준</strong>입니다 — 이전 회차에서 분석한
    오류도 "분석 완료"로 보입니다.
    <span className="mt-1.5 block">
      덕분에 같은 오류를 회차마다 다시 분석(= 중복 과금)하지 않습니다.
    </span>
  </>
);

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
          run
            ? `${formatDateTime(run.range_start)} ~ ${formatDateTime(run.range_end)} 구간을 ${formatRelative(run.started_at)} 실행했습니다.`
            : '이 회차가 묶은 오류 그룹입니다.'
        }
        info={
          <>
            {FINGERPRINT_NOTE}
            {run && (
              <span className="mt-1.5 block">
                실행 시각 {formatDateTime(run.started_at)}.
              </span>
            )}
          </>
        }
        actions={
          <ButtonLink to="/policies">
            <PolicyIcon aria-hidden className="size-4" />
            정책 목록
          </ButtonLink>
        }
      />

      {/* 회차 요약은 네 칸으로 자리가 정해져 있다 — 스켈레톤이 거짓 신호를 주지 않는다. */}
      {runQuery.isPending && <SkeletonStats count={4} label="조회 요약을 불러오는 중" />}
      {runQuery.isError && <ErrorBlock error={runQuery.error} />}

      {run && (
        <PageStack>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="조회 라인"
              icon={ErrorGroupIcon}
              value={formatNumber(run.fetched_count)}
              sub="실제 조회 라인 수"
              info={
                <>
                  로그 소스에서 <strong>실제로 읽어 온 로그 라인 수</strong>입니다 — 대시보드의{' '}
                  <Code>count_over_time</Code> metric 건수와 다른 값입니다.
                  <span className="mt-1.5 block">
                    라인 수 상한은 <strong>서버가</strong> 강제하므로 이 값은 정책의{' '}
                    <Code>max_lines</Code> 를 넘지 않습니다.
                  </span>
                </>
              }
              tone="accent"
            />
            <Stat
              label="제외 라인"
              value={formatNumber(run.dropped_count)}
              sub="제외 정규식·파싱 실패"
            />
            <Stat
              label="오류 그룹"
              icon={GroupCountIcon}
              value={formatNumber(run.group_count)}
              sub="fingerprint 단위"
              info={
                <>
                  마스킹 → 정규화 → fingerprint 순으로 처리한 뒤 같은 fingerprint 를 하나로 묶은
                  수입니다.
                </>
              }
            />
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
            <Card
              title="조회 경고"
              description="조정·누락 사실은 경고 코드로 남습니다."
              info={
                <>
                  경고는 <strong>기록</strong>입니다 — 무언가를 자동으로 실행하지 않습니다.
                  <span className="mt-1.5 block">
                    예: <Code>range_clamped</Code> 는 요청한 기간이 정책·서버 상한으로 줄었다는
                    뜻이고, 조회 자체는 성공입니다.
                  </span>
                </>
              }
            >
              <ul className="space-y-2 text-sm">
                {run.warnings.map((warning, index) => (
                  <li key={`${warning.code}-${index}`} className="flex flex-wrap items-baseline gap-2">
                    <Badge tone="warning">{warningCodeLabel(warning.code)}</Badge>
                    <span className="text-ink-soft">
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
            description="유사 오류를 fingerprint 로 묶은 결과입니다. 그룹을 누르면 대표 로그와 AI 분석으로 갑니다."
            info={
              <>
                대표 로그는 <strong>마스킹된 값</strong>만 저장됩니다 — 마스킹 전 원본은 DB 에
                남기지 않습니다.
                <span className="mt-1.5 block">{FINGERPRINT_NOTE}</span>
              </>
            }
          >
            {groupsQuery.isPending && <LoadingBlock label="오류 그룹을 불러오는 중…" />}
            {groupsQuery.isError && <ErrorBlock error={groupsQuery.error} />}
            {groupsQuery.data && groupsQuery.data.items.length === 0 && (
              <EmptyBlock icon={ErrorGroupIcon}>
                이 회차에서 묶인 오류 그룹이 없습니다.
              </EmptyBlock>
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
                    <tr key={group.id} className="transition-colors hover:bg-surface-2">
                      <Td>
                        <TextLink
                          to={`/error-groups/${group.id}`}
                          title={group.normalized_message}
                        >
                          {truncate(group.normalized_message, 90)}
                        </TextLink>
                        <p className="mt-0.5 text-xs text-muted">
                          {group.service ?? '(서비스 라벨 없음)'}
                          {group.environment ? ` · ${group.environment}` : ''}
                          {group.error_type ? ` · ${group.error_type}` : ''}
                        </p>
                      </Td>
                      <Td align="right" className="font-semibold text-ink">
                        {formatNumber(group.count)}
                      </Td>
                      <Td className="whitespace-nowrap text-muted">
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
        </PageStack>
      )}
    </div>
  );
}
