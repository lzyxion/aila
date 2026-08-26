/**
 * 전체 오류 그룹 (`/error-groups`) — 계약 4.
 *
 * 정책 카드를 하나씩 열지 않아도 "지금 가장 많이 터지는 오류"가 한 화면에 나온다. 대상은
 * 전 활성 정책의 **최신 성공 query-run** 그룹이고 정렬은 count desc, last_seen desc 다.
 *
 * 심각도 배경색 규칙(계약):
 * - 색은 기존 **severity 배지 팔레트의 옅은 톤**만 쓴다 (새 색을 도입하지 않는다).
 * - **색만으로 구분하지 않는다** — 같은 행에 `SeverityBadge` 를 반드시 병기한다. 색각
 *   이상·흑백 출력·강제 대비 모드에서 색은 사라지지만 글자는 남는다.
 * - `severity` 는 **LLM 추정**이고 발생량 기반 지표가 아니다. 미분석 그룹은 색을 칠하지
 *   않는다 — 회색으로 칠하면 "정보 등급"과 섞인다.
 *
 * 같은 목록을 통합 대시보드 하단(`AllErrorGroupsPanel`)이 재사용한다 — 두 곳에서 정렬이나
 * 색 규칙이 갈리면 같은 오류가 화면마다 다른 등급으로 보인다.
 */

import { useState } from 'react';
import { Link } from 'react-router';

import { isEndpointMissing } from '../api/client';
import { useDashboardErrorGroups } from '../api/queries';
import {
  AnalysisStatusBadge,
  SeverityBadge,
  severityRowClass,
} from '../components/StatusBadges';
import {
  Button,
  Card,
  EmptyBlock,
  ErrorBlock,
  LoadingBlock,
  Notice,
  PageHeader,
  TableWrap,
  Td,
  Th,
  cx,
} from '../components/ui';
import { formatDateTime, formatNumber, formatRelative, truncate } from '../lib/format';

export function ErrorGroupsPage() {
  return (
    <div>
      <PageHeader
        title="오류 그룹"
        description={
          <>
            전 활성 정책의 <strong>최신 성공 조회</strong>에서 묶인 그룹을 발생 수 순으로 모은
            목록입니다. 분석 상태는 그룹 id 가 아니라 <strong>fingerprint 기준</strong>이라 이전
            회차에서 분석한 오류도 "분석 완료"로 보입니다.
          </>
        }
      />
      <AllErrorGroupsPanel pageSize={25} />
    </div>
  );
}

/**
 * 목록 본체. 통합 대시보드 하단과 전용 페이지가 같은 컴포넌트를 쓴다.
 *
 * `compact` 는 대시보드용 — 페이지네이션 대신 "전체 보기" 링크 하나만 둔다 (첫 화면에서
 * 목록을 넘기기 시작하면 그 아래 아무것도 없다는 사실이 묻힌다).
 */
export function AllErrorGroupsPanel({
  pageSize = 20,
  compact = false,
}: {
  pageSize?: number;
  compact?: boolean;
}) {
  const [offset, setOffset] = useState(0);
  const query = useDashboardErrorGroups({ limit: pageSize, offset });

  const items = query.data?.items ?? [];
  const total = query.data?.total ?? 0;

  return (
    <Card
      title="전체 오류 그룹"
      description={
        compact
          ? '전 활성 정책의 최신 성공 조회 기준 · 발생 수 순'
          : '행을 누르면 마스킹된 대표 로그와 AI 분석으로 들어갑니다.'
      }
      actions={
        <div className="flex items-center gap-2">
          {total > 0 && (
            <span className="text-xs text-slate-500">
              전체 <strong className="text-slate-800">{formatNumber(total)}</strong>개
              {!compact && total > pageSize && ` 중 ${offset + 1}–${Math.min(offset + pageSize, total)}`}
            </span>
          )}
          {compact && (
            <Link
              to="/error-groups"
              className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-800 hover:bg-slate-50"
            >
              전체 보기 →
            </Link>
          )}
        </div>
      }
    >
      {query.isPending && <LoadingBlock label="오류 그룹을 불러오는 중…" />}

      {/* 백엔드에 아직 경로가 없으면 실패가 아니라 안내다 — 정책 카드는 그대로 남는다. */}
      {query.isError &&
        (isEndpointMissing(query.error) ? (
          <Notice tone="warning" title="전체 오류 그룹 API 를 아직 쓸 수 없습니다">
            <code className="rounded bg-white/60 px-1">GET /api/dashboard/error-groups</code> 가
            응답하지 않습니다. 백엔드에 이 경로가 올라오면 전 정책의 오류 그룹이 여기에 한 목록으로
            모입니다. 그 전에는 정책 카드의 <strong>그룹 보기</strong>로 회차별 목록을 여십시오.
          </Notice>
        ) : (
          <ErrorBlock error={query.error} />
        ))}

      {query.data && items.length === 0 && (
        <EmptyBlock>
          성공한 조회에서 묶인 오류 그룹이 없습니다. 정책을 한 번 실행하십시오.
        </EmptyBlock>
      )}

      {items.length > 0 && (
        <>
          <TableWrap minWidth="58rem">
            <thead>
              <tr>
                <Th>정책</Th>
                <Th>서비스</Th>
                <Th>메시지</Th>
                <Th align="right" className="whitespace-nowrap">
                  발생 수
                </Th>
                <Th className="whitespace-nowrap">마지막 발생</Th>
                <Th className="whitespace-nowrap">심각도 · 분석</Th>
              </tr>
            </thead>
            <tbody>
              {items.map((group) => (
                <tr
                  key={`${group.policy_id}-${group.id}`}
                  // 배경은 심각도의 옅은 톤 + 왼쪽 테두리. 배지를 함께 두므로 색이
                  // 사라져도 등급이 읽힌다.
                  className={cx(
                    'transition-colors hover:brightness-[0.98]',
                    severityRowClass(group.latest_severity),
                  )}
                >
                  <Td className="whitespace-nowrap">
                    <Link
                      to={`/dashboard/${group.policy_id}`}
                      className="text-xs font-medium text-sky-800 hover:underline"
                    >
                      {truncate(group.policy_name, 28)}
                    </Link>
                    <p className="mt-0.5 text-xs text-slate-400">#{group.policy_id}</p>
                  </Td>
                  <Td className="whitespace-nowrap">
                    <p className="text-sm text-slate-800">{group.service ?? '(라벨 없음)'}</p>
                    <p className="mt-0.5 text-xs text-slate-500">
                      {group.environment ?? '-'}
                      {group.error_type ? ` · ${group.error_type}` : ''}
                    </p>
                  </Td>
                  <Td>
                    <Link
                      to={`/error-groups/${group.id}`}
                      className="font-medium text-sky-800 hover:underline"
                      title={group.normalized_message}
                    >
                      {truncate(group.normalized_message, 84)}
                    </Link>
                    <p className="mt-0.5 font-mono text-xs text-slate-500">{group.fingerprint}</p>
                  </Td>
                  <Td align="right" className="font-semibold text-slate-900">
                    {formatNumber(group.count)}
                  </Td>
                  <Td className="whitespace-nowrap text-sm">
                    <span title={formatDateTime(group.last_seen)}>
                      {formatRelative(group.last_seen)}
                    </span>
                  </Td>
                  <Td className="whitespace-nowrap">
                    <div className="flex flex-col items-start gap-1">
                      {/* 색만으로 구분하지 않기 위한 병기 — 배경색과 짝을 이룬다. */}
                      <SeverityBadge severity={group.latest_severity} />
                      <AnalysisStatusBadge status={group.analysis_status} />
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>

          <p className="mt-3 text-xs text-slate-500">
            행 배경은 <strong>LLM 추정 심각도</strong>이며 발생 수와 무관합니다. 색이 보이지 않는
            환경을 위해 각 행에 배지를 함께 표시합니다.
          </p>

          {!compact && total > pageSize && (
            <div className="mt-3 flex items-center justify-end gap-2">
              <Button
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - pageSize))}
              >
                ← 이전
              </Button>
              <span className="text-xs text-slate-500 tabular-nums">
                {Math.floor(offset / pageSize) + 1} / {Math.max(1, Math.ceil(total / pageSize))}
              </span>
              <Button
                size="sm"
                disabled={offset + pageSize >= total}
                onClick={() => setOffset(offset + pageSize)}
              >
                다음 →
              </Button>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
