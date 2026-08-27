/**
 * 상태 배지.
 *
 * 표기 규칙(계약):
 * - `severity` 는 **LLM 추정** 심각도다. 발생량 기반 지표와 섞이지 않게 항상 "LLM 추정"을 붙인다.
 * - 분석 상태는 그룹 id 가 아니라 fingerprint 기준으로 온 값을 그대로 보여준다.
 */

import type {
  AnalysisJobStatus,
  QueryRunStatus,
  Severity,
  TriggeredBy,
  UsageStatus,
  UserRole,
} from '../api/types';
import {
  formatIntervalMinutes,
  jobStatusLabel,
  queryRunStatusLabel,
  severityLabel,
  usageStatusLabel,
} from '../lib/format';
import { WarningIcon } from './icons';
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
          <Spinner className="size-3 border-sky-300 border-t-sky-700 dark:border-sky-800 dark:border-t-sky-300" />
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

/**
 * 심각도별 행 배경 — **배지 팔레트의 옅은 톤만** 쓴다 (새 색을 만들지 않는다).
 *
 * 두 가지 규칙이 있다.
 * 1. **색만으로 구분하지 않는다.** 이 배경을 쓰는 행에는 `SeverityBadge` 를 반드시 함께
 *    둔다 — 색각 이상·흑백 출력·다크 모드 강제 반전에서 색은 사라지지만 글자는 남는다.
 * 2. 본문 글자는 `text-slate-700` 이상을 유지한다. 50 단계 배경은 그 대비를 깨지 않는
 *    가장 옅은 톤이고, 왼쪽 테두리(200~400)가 색 차이를 배경보다 크게 벌려 준다.
 *
 * 심각도가 없으면(미분석) 배경을 칠하지 않는다 — 회색으로 칠하면 "정보 등급"과 섞인다.
 *
 * 다크에서는 950 배경 + 700~800 테두리로 뒤집는다. 어두운 화면에서 50 단계 배경은
 * 행 전체가 하얗게 타서 본문 글자를 밀어낸다 — 옅은 톤이라는 성질을 유지하되 방향을
 * 뒤집는 것이지, 색 자체를 바꾸지 않는다 (rose 는 다크에서도 rose 다).
 */
const severityRowTone: Record<Severity, string> = {
  critical: 'bg-rose-50 border-l-4 border-rose-400 dark:bg-rose-950/50 dark:border-rose-700',
  high: 'bg-rose-50 border-l-4 border-rose-300 dark:bg-rose-950/40 dark:border-rose-800',
  medium: 'bg-amber-50 border-l-4 border-amber-300 dark:bg-amber-950/40 dark:border-amber-800',
  low: 'bg-sky-50 border-l-4 border-sky-300 dark:bg-sky-950/40 dark:border-sky-800',
  info: 'bg-slate-50 border-l-4 border-slate-300 dark:bg-slate-800/40 dark:border-slate-700',
};

export function severityRowClass(severity: Severity | null | undefined): string {
  return severity ? severityRowTone[severity] : 'border-l-4 border-transparent';
}

/** 항상 "LLM 추정" 표기를 붙인다. 발생량 기반 심각도가 아니다. */
export function SeverityBadge({ severity }: { severity: Severity | null | undefined }) {
  // 값 없음은 **0 도 '정보 등급'도 아니다** — 배지 대신 '-' 로 비운다.
  if (!severity) return <span className="text-xs text-faint">-</span>;
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

// ------------------------------------------------------- 수집 중단 (Phase 7)

/**
 * 수집 중단 의심 배지 (`ingest_absent`).
 *
 * 연결에 적힌 `expected_services` 중 조회 기간에 로그를 **한 줄도 내지 않은** 서비스가
 * 있다는 뜻이다. 오류가 0 건인 것과 로그 자체가 끊긴 것은 정반대의 사건인데 화면에서는
 * 똑같이 "조용한 정책"으로 보이기 때문에 눈에 띄는 자리에 둔다.
 *
 * 표기 규칙 두 가지:
 * - **색만으로 구분하지 않는다.** 배지에 "수집 중단 의심"이라는 글자를 반드시 함께 싣고,
 *   서비스 이름은 메시지에서 그대로 가져와 배지 옆에 적는다 (색각 이상·흑백 출력 대비).
 * - 이건 **기록일 뿐 알림이 아니다.** 배지를 눌러 무언가가 자동으로 실행되지 않는다
 *   (계약: 자동 트리거는 정책의 `auto_analyze_new` 하나뿐이다).
 */
export function IngestAbsentBadge({
  warning,
  compact = false,
}: {
  warning: { code: string; message: string; count?: number | null };
  compact?: boolean;
}) {
  return (
    <Badge tone="danger" title={warning.message} className={compact ? undefined : 'max-w-full'}>
      <WarningIcon aria-hidden className="size-3.5 shrink-0" />
      수집 중단 의심
      {warning.count != null && warning.count > 1 ? ` · ${warning.count}개 서비스` : ''}
    </Badge>
  );
}

// ------------------------------------------------------------------ 권한·역할

export function RoleBadge({ role }: { role: UserRole }) {
  return role === 'admin' ? (
    <Badge tone="accent" title="정책 실행·AI 분석·설정 변경을 할 수 있습니다.">
      admin
    </Badge>
  ) : (
    <Badge
      tone="neutral"
      title="읽기 전용 계정입니다 — 조회는 할 수 있지만 실행·저장·삭제는 admin 만 할 수 있습니다."
    >
      viewer · 읽기 전용
    </Badge>
  );
}

// ------------------------------------------------------------------ 스케줄

/**
 * 정책의 스케줄 상태.
 *
 * 꺼져 있으면 아무것도 그리지 않는다 — 정책 목록에서 "수동" 배지가 모든 행에 붙으면
 * 정작 켜진 정책이 눈에 띄지 않는다.
 */
export function ScheduleBadge({
  enabled,
  intervalMinutes,
  autoAnalyze = false,
}: {
  enabled: boolean;
  intervalMinutes: number | null;
  autoAnalyze?: boolean;
}) {
  if (!enabled) return null;
  return (
    <>
      <Badge
        tone="info"
        title={
          intervalMinutes
            ? `${formatIntervalMinutes(intervalMinutes)}마다 이 정책으로 로그 소스를 조회합니다.`
            : '스케줄이 켜져 있지만 주기가 설정되지 않았습니다.'
        }
      >
        {intervalMinutes ? `스케줄 ${formatIntervalMinutes(intervalMinutes)}` : '스케줄 (주기 없음)'}
      </Badge>
      {autoAnalyze && (
        <Badge
          tone="warning"
          title="스케줄 조회에서 처음 보는 오류에만 분석이 실행되고, 일일 분석 한도의 제한을 받습니다. 비용이 나가는 경로입니다."
        >
          신규 그룹 자동 분석
        </Badge>
      )}
    </>
  );
}

/**
 * 실행 주체 배지 (수동/자동).
 *
 * 값이 없으면(백엔드가 아직 필드를 안 내려줌) 아무것도 그리지 않는다 — 없는 값을
 * "수동"으로 채워 보여주면 스케줄이 돌기 시작한 뒤에도 전부 수동으로 보인다.
 */
export function TriggeredByBadge({ value }: { value: TriggeredBy | null | undefined }) {
  if (value !== 'manual' && value !== 'schedule') return null;
  return value === 'schedule' ? (
    <Badge tone="info" title="스케줄러가 자동으로 실행했습니다.">
      자동
    </Badge>
  ) : (
    <Badge tone="neutral" title="사람이 화면에서 실행했습니다.">
      수동
    </Badge>
  );
}
