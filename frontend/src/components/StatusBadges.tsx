/**
 * 상태 배지.
 *
 * 표기 규칙(계약):
 * - `severity` 는 **LLM 추정** 심각도다. 발생량 기반 지표와 섞이지 않게 항상 "LLM 추정"을 붙인다.
 * - 분석 상태는 그룹 id 가 아니라 fingerprint 기준으로 온 값을 그대로 보여준다.
 */

import type { AnalysisJobStatus, QueryRunStatus, Severity, UsageStatus } from '../api/types';
import { jobStatusLabel, queryRunStatusLabel, severityLabel, usageStatusLabel } from '../lib/format';
import { Badge, Spinner } from './ui';

export function AnalysisStatusBadge({
  status,
  compact = false,
}: {
  status: AnalysisJobStatus | null | undefined;
  compact?: boolean;
}) {
  const label = jobStatusLabel(status);
  switch (status) {
    case 'succeeded':
      return <Badge tone="success">{label}</Badge>;
    case 'failed':
      return <Badge tone="danger">{label}</Badge>;
    case 'running':
    case 'pending':
      return (
        <Badge tone="info">
          <Spinner className="size-3 border-sky-300 border-t-sky-700" />
          {label}
        </Badge>
      );
    default:
      return (
        <Badge tone="neutral" title={compact ? undefined : 'fingerprint 기준으로 분석 이력이 없습니다'}>
          {label}
        </Badge>
      );
  }
}

const severityTone: Record<Severity, 'danger' | 'warning' | 'info' | 'neutral'> = {
  critical: 'danger',
  high: 'danger',
  medium: 'warning',
  low: 'info',
  info: 'neutral',
};

/** 항상 "LLM 추정" 표기를 붙인다. 발생량 기반 심각도가 아니다. */
export function SeverityBadge({ severity }: { severity: Severity | null | undefined }) {
  if (!severity) return <span className="text-xs text-slate-400">-</span>;
  return (
    <Badge
      tone={severityTone[severity]}
      title="LLM 이 대표 로그 몇 건으로 추정한 값입니다. 발생량 기반 지표가 아닙니다."
    >
      LLM 추정 · {severityLabel(severity)}
    </Badge>
  );
}

export function QueryRunStatusBadge({ status }: { status: QueryRunStatus }) {
  const tone =
    status === 'succeeded'
      ? 'success'
      : status === 'failed'
        ? 'danger'
        : ('info' as const);
  return <Badge tone={tone}>{queryRunStatusLabel(status)}</Badge>;
}

export function UsageStatusBadge({ status }: { status: UsageStatus }) {
  return (
    <Badge tone={status === 'succeeded' ? 'success' : 'danger'}>{usageStatusLabel(status)}</Badge>
  );
}
