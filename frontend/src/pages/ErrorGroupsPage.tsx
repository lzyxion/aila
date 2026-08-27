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
 *
 * Phase 8: 표 아래에 있던 색 규칙 설명은 **지운 것이 아니라** 카드 제목의 ⓘ 로 옮겼다.
 */

import { useState } from 'react';

import { isEndpointMissing } from '../api/client';
import { useDashboardErrorGroups } from '../api/queries';
import { ErrorGroupIcon } from '../components/icons';
import {
  AnalysisStatusBadge,
  SeverityBadge,
  severityRowClass,
} from '../components/StatusBadges';
import {
  Button,
  ButtonLink,
  Card,
  Code,
  EmptyBlock,
  ErrorBlock,
  LoadingBlock,
  Notice,
  PageHeader,
  TableWrap,
  Td,
  TextLink,
  Th,
  cx,
} from '../components/ui';
import { formatDateTime, formatNumber, formatRelative, truncate } from '../lib/format';

/** 두 화면(전용 페이지·홈 하단)이 같은 문구를 쓰도록 한 곳에서 만든다. */
const SCOPE_NOTE = (
  <>
    대상은 전 <strong>활성</strong> 정책의 <strong>최신 성공</strong> 조회 회차입니다 — 비활성
    정책을 섞으면 이미 끄기로 한 오류가 상위를 차지하고, 회차를 좁히지 않으면 같은 오류가 회차
    수만큼 중복됩니다.
    <span className="mt-1.5 block">
      분석 상태는 그룹 id 가 아니라 <strong>fingerprint 기준</strong>이라 이전 회차에서 분석한
      오류도 "분석 완료"로 보입니다.
    </span>
    <span className="mt-1.5 block">
      서로 겹치는 정책이 같은 오류를 잡으면 같은 fingerprint 가 정책 수만큼 나옵니다 — 중복이
      아니라 "두 정책이 같은 오류를 보고 있다"는 사실이며 <strong>정책</strong> 열로 구분됩니다.
    </span>
  </>
);

const SEVERITY_COLOR_NOTE = (
  <>
    행 배경은 <strong>LLM 추정 심각도</strong>이며 발생 수와 무관합니다.
    <span className="mt-1.5 block">
      색이 보이지 않는 환경(색각 이상·흑백 출력·강제 대비)을 위해 각 행에 배지를 함께 표시합니다
      — 색은 보조 신호일 뿐입니다. 미분석 그룹은 아예 칠하지 않습니다.
    </span>
  </>
);

export function ErrorGroupsPage() {
  return (
    <div>
      <PageHeader
        title="오류 그룹"
        description="전 활성 정책의 최신 성공 조회에서 묶인 그룹을 발생 수 순으로 모은 목록입니다."
        info={SCOPE_NOTE}
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
      info={
        <>
          {SEVERITY_COLOR_NOTE}
          {/* 전용 페이지에서는 범위 설명이 이미 페이지 머리말의 ⓘ 에 있다. */}
          {compact && <span className="mt-1.5 block">{SCOPE_NOTE}</span>}
        </>
      }
      actions={
        <div className="flex items-center gap-2">
          {total > 0 && (
            <span className="text-xs text-muted tabular-nums">
              전체 <strong className="text-ink-soft">{formatNumber(total)}</strong>개
              {!compact && total > pageSize && ` 중 ${offset + 1}–${Math.min(offset + pageSize, total)}`}
            </span>
          )}
          {compact && (
            <ButtonLink to="/error-groups" size="sm">
              전체 보기 →
            </ButtonLink>
          )}
        </div>
      }
    >
      {/*
        결과 개수를 모르는 목록이라 스켈레톤 행을 그리지 않는다 — "N건 있다"는 거짓 신호가
        되기 때문이다. 여기서는 스피너가 정직하다.
      */}
      {query.isPending && <LoadingBlock label="오류 그룹을 불러오는 중…" />}

      {/* 백엔드에 아직 경로가 없으면 실패가 아니라 안내다 — 정책 카드는 그대로 남는다. */}
      {query.isError &&
        (isEndpointMissing(query.error) ? (
          <Notice tone="warning" title="전체 오류 그룹 API 를 아직 쓸 수 없습니다">
            <Code>GET /api/dashboard/error-groups</Code> 가 응답하지 않습니다. 백엔드에 이 경로가
            올라오면 전 정책의 오류 그룹이 여기에 한 목록으로 모입니다. 그 전에는 정책 카드의{' '}
            <strong>그룹 보기</strong>로 회차별 목록을 여십시오.
          </Notice>
        ) : (
          <ErrorBlock error={query.error} />
        ))}

      {query.data && items.length === 0 && (
        <EmptyBlock icon={ErrorGroupIcon}>
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
                    <TextLink to={`/dashboard/${group.policy_id}`} className="text-xs">
                      {truncate(group.policy_name, 28)}
                    </TextLink>
                    <p className="mt-0.5 text-xs text-faint tabular-nums">#{group.policy_id}</p>
                  </Td>
                  <Td className="whitespace-nowrap">
                    <p className="text-sm text-ink-soft">{group.service ?? '(라벨 없음)'}</p>
                    <p className="mt-0.5 text-xs text-muted">
                      {group.environment ?? '-'}
                      {group.error_type ? ` · ${group.error_type}` : ''}
                    </p>
                  </Td>
                  <Td>
                    <TextLink to={`/error-groups/${group.id}`} title={group.normalized_message}>
                      {truncate(group.normalized_message, 84)}
                    </TextLink>
                    <p className="mt-0.5 font-mono text-xs text-faint">{group.fingerprint}</p>
                  </Td>
                  <Td align="right" className="font-semibold text-ink">
                    {formatNumber(group.count)}
                  </Td>
                  <Td className="whitespace-nowrap text-sm text-muted">
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

          {!compact && total > pageSize && (
            <div className="mt-4 flex items-center justify-end gap-2">
              <Button
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - pageSize))}
              >
                ← 이전
              </Button>
              <span className="text-xs text-muted tabular-nums">
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
