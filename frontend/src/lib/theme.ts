/**
 * 테마(light / dark / system) 한 곳.
 *
 * **Provider 가 없다.** 모듈 수준 스토어 + `useSyncExternalStore` 다. 이유는 두 가지다.
 * 1. 테마 토글은 로그인 화면(`Layout` 바깥)에도 있어야 한다 — Provider 를 쓰면 라우터
 *    트리 어디에 꽂을지가 매번 문제가 된다.
 * 2. 차트는 지연 로딩된다 (`chartsLazy`). 컨텍스트 없이 값을 읽을 수 있어야 청크가
 *    늦게 붙어도 색이 맞는다.
 *
 * 상태는 세 가지이고 저장되는 것도 세 가지다 — `system` 을 "저장 안 함"으로 표현하면
 * "OS 를 따라가겠다"와 "아직 정한 적 없다"를 구분할 수 없다.
 *
 * | mode | html.dark | 저장값 |
 * | --- | --- | --- |
 * | `light` | 없음 | `'light'` |
 * | `dark` | 있음 | `'dark'` |
 * | `system` | prefers-color-scheme 을 따라 붙었다 뗐다 | `'system'` |
 *
 * 첫 페인트의 흰 번쩍임은 여기가 아니라 `index.html` 의 인라인 스니펫이 막는다 —
 * 번들이 평가되기 전에 클래스가 이미 붙어 있어야 하기 때문이다. **둘의 키·판정 규칙이
 * 같아야 한다** (`aila-theme`, 아래 `resolve`).
 */

import { useSyncExternalStore } from 'react';

export type ThemeMode = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'aila-theme';

const DARK_QUERY = '(prefers-color-scheme: dark)';

function isMode(value: unknown): value is ThemeMode {
  return value === 'light' || value === 'dark' || value === 'system';
}

/** localStorage 는 사파리 프라이빗 모드 등에서 던진다 — 실패는 "정한 적 없음"으로 본다. */
function readStoredMode(): ThemeMode {
  if (typeof window === 'undefined') return 'system';
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isMode(raw) ? raw : 'system';
  } catch {
    return 'system';
  }
}

function systemPrefersDark(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia(DARK_QUERY).matches;
}

function resolve(mode: ThemeMode): ResolvedTheme {
  if (mode === 'system') return systemPrefersDark() ? 'dark' : 'light';
  return mode;
}

let mode: ThemeMode = readStoredMode();
let resolved: ResolvedTheme = resolve(mode);

const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

/** DOM 반영은 한 곳에서만 한다 — 클래스와 color-scheme 이 어긋나면 스크롤바만 반대로 뜬다. */
function applyToDocument(next: ResolvedTheme): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.classList.toggle('dark', next === 'dark');
  root.style.colorScheme = next;
}

function recompute(): void {
  const next = resolve(mode);
  if (next === resolved) return;
  resolved = next;
  applyToDocument(resolved);
  emit();
}

export function setThemeMode(next: ThemeMode): void {
  if (mode === next) return;
  mode = next;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch {
    /* 저장 못 해도 이번 세션 동안은 동작한다 — 막을 이유가 없다. */
  }
  const nextResolved = resolve(mode);
  if (nextResolved !== resolved) {
    resolved = nextResolved;
    applyToDocument(resolved);
  }
  emit();
}

export function getThemeMode(): ThemeMode {
  return mode;
}

export function getResolvedTheme(): ResolvedTheme {
  return resolved;
}

/**
 * OS 설정 변화 구독. `mode === 'system'` 일 때만 의미가 있지만 항상 붙여 둔다 —
 * 붙였다 뗐다 하면 모드를 바꾸는 순간의 상태 전이를 놓친다.
 */
let mediaCleanup: (() => void) | null = null;

function subscribe(listener: () => void): () => void {
  if (listeners.size === 0 && typeof window !== 'undefined' && window.matchMedia) {
    const query = window.matchMedia(DARK_QUERY);
    query.addEventListener('change', recompute);
    mediaCleanup = () => query.removeEventListener('change', recompute);
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && mediaCleanup) {
      mediaCleanup();
      mediaCleanup = null;
    }
  };
}

/** 현재 모드(사용자가 고른 3상태). 토글 UI 가 쓴다. */
export function useThemeMode(): ThemeMode {
  return useSyncExternalStore(subscribe, getThemeMode, () => 'system' as ThemeMode);
}

/** 실제로 적용된 두 상태. 차트처럼 **색 값**이 필요한 쪽이 쓴다. */
export function useResolvedTheme(): ResolvedTheme {
  return useSyncExternalStore(subscribe, getResolvedTheme, () => 'light' as ResolvedTheme);
}

export const THEME_LABEL: Record<ThemeMode, string> = {
  light: '밝게',
  dark: '어둡게',
  system: '시스템',
};
