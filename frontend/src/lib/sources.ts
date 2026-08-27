/**
 * 로그 소스 종류의 표시 어휘 — 한 곳에 모아 둔다.
 *
 * 새 어댑터(예: elasticsearch)가 오면 여기 두 맵에 한 줄씩 추가하는 것으로
 * 연결 폼의 선택지와 정책 화면의 쿼리 언어 라벨이 함께 따라온다.
 */

import type { SourceType } from '../api/types';

/** 화면 표시용 소스 이름. */
export const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  loki: 'Grafana Loki',
};

/** 소스별 쿼리 언어 이름 — "쿼리 (LogQL)" 처럼 병기할 때 쓴다. */
export const QUERY_LANGUAGE_BY_SOURCE: Record<SourceType, string> = {
  loki: 'LogQL',
};

export function sourceTypeLabel(sourceType: SourceType): string {
  return SOURCE_TYPE_LABELS[sourceType] ?? sourceType;
}

/** 알 수 없는 소스면 null — 라벨은 언어 병기 없이 "쿼리"로만 나간다. */
export function queryLanguageOf(sourceType: SourceType | null | undefined): string | null {
  if (!sourceType) return null;
  return QUERY_LANGUAGE_BY_SOURCE[sourceType] ?? null;
}
