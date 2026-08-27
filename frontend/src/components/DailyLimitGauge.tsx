/**
 * 일일 분석 한도 게이지 (Phase 7 — `GET /api/usage/daily-limit`).
 *
 * 답해야 하는 질문은 하나다: **"오늘 분석을 몇 번 더 돌릴 수 있는가."** 사용량 화면의
 * 토큰·비용은 지나간 일을 보여주지만, 지금 버튼을 눌렀을 때 429 가 나는지는 이 숫자만
 * 답할 수 있다.
 *
 * 표기 규칙:
 * - "오늘"의 경계는 429 를 내는 한도 검사와 **같은 계산**이다(`app_settings.timezone` 의
 *   로컬 자정). 그래서 기준 날짜와 타임존 이름을 화면에 그대로 적는다 — 서버가 UTC 로
 *   돌아도 기본값이면 KST 자정에 리셋되고, 그 사실이 화면에 없으면 "왜 아직 안 줄었지"가 된다.
 * - **색만으로 구분하지 않는다.** 소진 정도는 막대 색과 함께 "남은 N회" 라는 글자로
 *   반드시 병기한다.
 * - 백엔드에 아직 경로가 없으면(404/405/501) 게이지를 **감춘다**. 한도를 모르는 상태를
 *   `0/0` 으로 그리면 "다 썼다"로 읽힌다.
 */

import { isEndpointMissing } from '../api/client';
import { useDailyLimit } from '../api/queries';
import type { DailyLimitResponse } from '../api/types';
import { formatNumber } from '../lib/format';
import { LimitIcon } from './icons';
import {
  Badge,
  Card,
  Code,
  EmptyBlock,
  ErrorBlock,
  Skeleton,
  SkeletonRegion,
  TableWrap,
  Td,
  Th,
  cx,
} from './ui';

/** 소진율에 따른 톤. 값과 문구는 호출부가 함께 적는다 (색만으로 구분하지 않는다). */
function toneFor(ratio: number): { bar: string; text: string; label: string } {
  if (ratio >= 1)
    return { bar: 'bg-rose-500', text: 'text-rose-700 dark:text-rose-300', label: '한도 소진' };
  if (ratio >= 0.8)
    return { bar: 'bg-amber-500', text: 'text-amber-700 dark:text-amber-300', label: '한도 임박' };
  return { bar: 'bg-accent', text: 'text-ink-soft', label: '여유' };
}

/**
 * 막대 하나. 전역 게이지와 정책별 행이 같은 규칙으로 그려져야 두 숫자를 비교할 수 있다.
 *
 * 한도가 0 이면 비율은 100% 로 그린다 — "0 중 0 을 썼으니 여유"가 아니라 **분석을 시작할
 * 수 없는 상태**다.
 */
export function LimitBar({
  used,
  limit,
  label,
  className,
}: {
  used: number;
  limit: number;
  label: string;
  className?: string;
}) {
  const ratio = limit > 0 ? Math.min(1, used / limit) : 1;
  const tone = toneFor(limit > 0 ? used / limit : 1);
  const remaining = Math.max(0, limit - used);

  return (
    <div className={className}>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={used}
        aria-label={`${label} — 한도 ${limit}회 중 ${used}회 사용`}
        className="h-2 w-full overflow-hidden rounded-full bg-surface-3"
      >
        <div className={cx('h-full rounded-full transition-all', tone.bar)} style={{ width: `${ratio * 100}%` }} />
      </div>
      <p className={cx('mt-1 text-xs', tone.text)}>
        {limit > 0 ? (
          <>
            {formatNumber(Math.round((used / limit) * 100))}% 사용 ·{' '}
            <strong>남은 {formatNumber(remaining)}회</strong>
            {used >= limit && ' · 다음 분석은 429 로 거절됩니다'}
          </>
        ) : (
          <strong>한도가 0 입니다 — 분석을 시작할 수 없습니다.</strong>
        )}
      </p>
    </div>
  );
}

/**
 * 전역 게이지 본체. 조회 훅을 안에서 부르므로 홈·사용량 화면이 같은 캐시를 공유한다.
 *
 * `compact` 는 홈 상단처럼 다른 요약과 나란히 놓일 때 쓴다 (카드 껍데기 없이 한 줄).
 */
export function DailyLimitGauge({ compact = false }: { compact?: boolean }) {
  const query = useDailyLimit();

  // 아직 경로가 없는 백엔드에서는 조용히 사라진다 — 실패로 보여줄 일이 아니다.
  if (query.isError && isEndpointMissing(query.error)) return null;
  if (query.isPending) {
    // compact 는 다른 요약과 한 줄에 놓이는 자리다 — 거기서는 자리만 비워 둔다.
    return compact ? null : (
      <SkeletonRegion label="오늘의 분석 한도를 불러오는 중">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="mt-2 h-2 w-full rounded-full" />
        <Skeleton className="mt-2 h-3 w-52" />
      </SkeletonRegion>
    );
  }
  if (query.isError) {
    return compact ? null : <ErrorBlock error={query.error} hint="일일 한도 소진 현황을 읽지 못했습니다." />;
  }

  const data = query.data as DailyLimitResponse;
  const remaining = Math.max(0, data.global_limit - data.global_used);

  const body = (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-semibold text-ink">
          오늘 분석{' '}
          <span className="tabular-nums">
            {formatNumber(data.global_used)} / {formatNumber(data.global_limit)}
          </span>
          <span className="ml-2 text-xs font-normal text-muted">
            남은 {formatNumber(remaining)}회
          </span>
        </p>
        <Badge
          tone="neutral"
          title="한도의 하루 경계는 이 타임존의 로컬 자정입니다 — 429 를 내는 서버 검사와 같은 계산입니다."
        >
          {data.date} · {data.timezone}
        </Badge>
      </div>
      <LimitBar
        className="mt-2"
        used={data.global_used}
        limit={data.global_limit}
        label="전역 일일 분석 한도"
      />
      {data.policies.length > 0 && (
        <p className="mt-1.5 text-xs text-muted">
          자체 한도를 가진 정책 {formatNumber(data.policies.length)}개는 이 한도와 <strong>따로</strong>{' '}
          더 낮은 상한을 받습니다.
        </p>
      )}
    </div>
  );

  if (compact) return body;

  return (
    <Card
      title="오늘의 분석 한도"
      description="한도를 넘긴 요청은 서버가 429 로 거절합니다."
      info={
        <>
          하루 경계는 <Code>app_settings.timezone</Code> 의 <strong>로컬 자정</strong>입니다 —
          429 를 내는 서버 검사와 같은 계산이라, 서버가 UTC 로 돌아도 기본값이면 KST 자정에
          리셋됩니다.
          <span className="mt-1.5 block">
            거절은 <strong>서버가</strong> 합니다. 화면이 버튼을 막는 것이 아닙니다.
          </span>
        </>
      }
    >
      {body}
    </Card>
  );
}

/**
 * 자체 한도를 가진 정책들의 소진 현황.
 *
 * **한도가 없는 정책은 오지 않는다**(계약) — 여기 다 실으면 "정책마다 별도 상한이 있다"로
 * 읽힌다. 목록이 비면 그 사실을 문장으로 적는다.
 */
export function PolicyDailyLimitTable() {
  const query = useDailyLimit();

  if (query.isError && isEndpointMissing(query.error)) return null;
  if (query.isPending || query.isError) return null;

  const data = query.data as DailyLimitResponse;

  return (
    <Card
      title="정책별 일일 한도"
      description="자체 한도를 가진 정책만 표시합니다."
      info={
        <>
          <Code>daily_analysis_limit</Code> 이 설정된 정책만 여기 나옵니다. 나머지 정책은 위의
          <strong> 전역 한도</strong>만 받습니다 — 목록이 비어 있다는 것은 "정책마다 별도 상한이
          있다"가 아니라 <strong>아무도 자체 상한을 두지 않았다</strong>는 뜻입니다.
        </>
      }
    >
      {data.policies.length === 0 ? (
        <EmptyBlock icon={LimitIcon}>
          자체 일일 한도를 설정한 정책이 없습니다 — 전부 전역 한도만 받습니다.
        </EmptyBlock>
      ) : (
        <TableWrap minWidth="32rem">
          <thead>
            <tr>
              <Th>정책</Th>
              <Th align="right">오늘 사용</Th>
              <Th align="right">한도</Th>
              <Th>소진</Th>
            </tr>
          </thead>
          <tbody>
            {data.policies.map((policy) => (
              <tr key={policy.policy_id} className="hover:bg-surface-2">
                <Td>
                  <p className="font-medium text-ink">{policy.name}</p>
                  <p className="mt-0.5 font-mono text-xs text-faint">#{policy.policy_id}</p>
                </Td>
                <Td align="right">{formatNumber(policy.used)}</Td>
                <Td align="right">{formatNumber(policy.limit)}</Td>
                <Td className="w-56">
                  <LimitBar used={policy.used} limit={policy.limit} label={`${policy.name} 일일 한도`} />
                </Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
    </Card>
  );
}
