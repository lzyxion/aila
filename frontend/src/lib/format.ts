/** 화면 표기 헬퍼. 표기 규칙(추정·가설·마스킹)은 계약이라 문구를 여기 모아 둔다. */

import type {
  AnalysisJobStatus,
  LLMProviderName,
  QueryRunStatus,
  Severity,
  TriggeredBy,
  UsageStatus,
} from '../api/types';

const dateTimeFormat = new Intl.DateTimeFormat('ko-KR', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

const timeFormat = new Intl.DateTimeFormat('ko-KR', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return dateTimeFormat.format(date);
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return timeFormat.format(date);
}

/** "3분 전" 같은 상대 시각. 마지막 발생 시각 표시에 쓴다. */
export function formatRelative(value: string | null | undefined): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diffSeconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (diffSeconds < 0) return '방금';
  if (diffSeconds < 60) return `${diffSeconds}초 전`;
  const minutes = Math.floor(diffSeconds / 60);
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  return `${Math.floor(hours / 24)}일 전`;
}

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-';
  return value.toLocaleString('ko-KR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '-';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} 초`;
}

/**
 * 추정 비용 표기. Decimal 이 JSON 에서 문자열로 오므로 문자열/숫자를 모두 받는다.
 * 호출부는 반드시 "추정" 표기를 함께 붙인다 — 정산 근거가 아니다.
 */
export function formatEstimatedCost(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '-';
  const numeric = typeof value === 'string' ? Number(value) : value;
  if (Number.isNaN(numeric)) return String(value);
  return `$${numeric.toLocaleString('ko-KR', {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  })}`;
}

export function formatTokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-';
  return `${value.toLocaleString('ko-KR')} tok`;
}

/**
 * 비율 표기 (0.023 -> "2.3%").
 *
 * **null 은 0% 가 아니다** — 분모 쿼리를 설정하지 않았거나 계산에 실패한 상태이고,
 * 0% 로 적으면 "오류가 하나도 없었다"로 읽힌다. 그래서 `-` 를 준다 (추정 비용과 같은 규칙).
 */
export function formatRatio(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '-';
  return `${(value * 100).toLocaleString('ko-KR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}%`;
}

/** 스케줄 주기 표기. 분 단위로 저장되지만 화면에서는 "6시간"이 읽기 쉽다. */
export function formatIntervalMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) return '-';
  if (minutes < 60) return `${minutes}분`;
  if (minutes % (60 * 24) === 0) return `${minutes / (60 * 24)}일`;
  if (minutes % 60 === 0) return `${minutes / 60}시간`;
  return `${Math.floor(minutes / 60)}시간 ${minutes % 60}분`;
}

// ------------------------------------------------------------------ 라벨

export function severityLabel(severity: Severity | null | undefined): string {
  switch (severity) {
    case 'critical':
      return '치명';
    case 'high':
      return '높음';
    case 'medium':
      return '보통';
    case 'low':
      return '낮음';
    case 'info':
      return '정보';
    default:
      return '-';
  }
}

export function jobStatusLabel(status: AnalysisJobStatus | null | undefined): string {
  switch (status) {
    case 'pending':
      return '대기 중';
    case 'running':
      return '분석 중';
    case 'succeeded':
      return '분석 완료';
    case 'failed':
      return '분석 실패';
    default:
      return '미분석';
  }
}

export function queryRunStatusLabel(status: QueryRunStatus | null | undefined): string {
  switch (status) {
    case 'pending':
      return '대기 중';
    case 'running':
      return '조회 중';
    case 'succeeded':
      return '조회 완료';
    case 'failed':
      return '조회 실패';
    default:
      return '-';
  }
}

/** 실행 주체. 값이 없으면 배지를 감추므로 여기서도 `-` 를 준다. */
export function triggeredByLabel(value: TriggeredBy | null | undefined): string {
  return value === 'schedule' ? '자동' : value === 'manual' ? '수동' : '-';
}

export function usageStatusLabel(status: UsageStatus | null | undefined): string {
  return status === 'failed' ? '실패' : status === 'succeeded' ? '성공' : '-';
}

export function providerLabel(provider: LLMProviderName | string): string {
  switch (provider) {
    case 'openai':
      return 'OpenAI';
    case 'anthropic':
      return 'Anthropic';
    case 'openai_compatible':
      return 'OpenAI 호환';
    default:
      return provider;
  }
}

export function authTypeLabel(authType: string): string {
  switch (authType) {
    case 'none':
      return '없음';
    case 'basic':
      return 'Basic';
    case 'bearer':
      return 'Bearer';
    case 'header':
      return '커스텀 헤더';
    default:
      return authType;
  }
}

/** FetchWarning.code 를 사람이 읽을 문구로. 모르는 코드는 그대로 보여준다. */
export function warningCodeLabel(code: string): string {
  switch (code) {
    case 'parse_error':
      return '파싱 실패';
    case 'limit_reached':
      return '라인 상한 도달';
    case 'partial_range':
      return '부분 구간만 조회됨';
    case 'entry_out_of_order':
      return '시각 역전 항목';
    // 한도 조정은 422 로 튕기지 않고 clamp + 경고로 남는다 (docs/DECISIONS.md).
    case 'range_clamped':
      return '기간 자동 조정';
    case 'limit_clamped':
      return '라인 수 자동 조정';
    case 'empty_result':
      return '결과 없음';
    case 'count_query_failed':
      return 'metric 쿼리 실패';
    case 'by_service_from_lines':
      return '서비스별 집계 폴백';
    // 통합 대시보드(summary)의 정책 단위 경고
    case 'policy_inactive':
      return '비활성 정책';
    case 'schedule_without_interval':
      return '스케줄 주기 없음';
    case 'last_run_failed':
      return '최근 실행 실패';
    case 'no_successful_run':
      return '성공한 실행 없음';
    case 'connection_unavailable':
      return '연결 사용 불가';
    case 'count_unsupported':
      return 'metric 미지원 소스';
    case 'count_query_timeout':
      return 'metric 쿼리 시간 초과';
    // Phase 7 — 수집 중단 확인 · 유입량 분모 쿼리
    case 'ingest_absent':
      return '수집 중단 의심';
    case 'presence_check_failed':
      return '수집 확인 실패';
    case 'baseline_query_failed':
      return '분모 쿼리 실패';
    default:
      return code;
  }
}

/**
 * 수집 중단 경고(`ingest_absent`)만 골라낸다.
 *
 * 여러 출처(조회 회차·overview·방금 실행한 결과)의 경고 목록이 같은 사실을 두 번 싣는
 * 일이 있으므로 **메시지 기준으로 중복을 접는다** — 같은 배지가 두 개 붙으면 "두 번
 * 일어난 일"로 읽힌다.
 */
export function ingestAbsentWarnings(
  ...groups: Array<Array<{ code: string; message: string; count?: number | null }> | undefined>
): Array<{ code: string; message: string; count?: number | null }> {
  const seen = new Set<string>();
  const rows: Array<{ code: string; message: string; count?: number | null }> = [];
  for (const group of groups) {
    for (const warning of group ?? []) {
      if (warning.code !== 'ingest_absent') continue;
      if (seen.has(warning.message)) continue;
      seen.add(warning.message);
      rows.push(warning);
    }
  }
  return rows;
}

export function truncate(text: string, max = 140): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/** `datetime-local` 입력값 <-> ISO 문자열 변환. */
export function toLocalInputValue(iso: string): string {
  const date = new Date(iso);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

export function fromLocalInputValue(local: string): string {
  return new Date(local).toISOString();
}
