/**
 * 테마 토글 (light / dark / system).
 *
 * **3상태를 3개의 버튼으로 보여준다.** 흔한 해 ↔ 달 두 상태 토글은 "지금 시스템을
 * 따라가는 중"이라는 상태를 표현하지 못한다 — OS 가 밤에 어두워지는 사용자는 자기가
 * 고정을 걸었는지 아닌지 화면에서 알 수 없게 된다.
 *
 * 표기 규칙:
 * - **그림만으로 구분하지 않는다.** 아이콘 버튼이지만 `aria-label`·`title` 에 글자가 있고,
 *   선택 상태는 색이 아니라 `aria-checked` + 눌린 면으로도 전달된다.
 * - `role="radiogroup"` — 세 개 중 하나를 고르는 것이지 각각을 켜고 끄는 게 아니다.
 */

import {
  THEME_LABEL,
  setThemeMode,
  useThemeMode,
  type ThemeMode,
} from '../lib/theme';
import { ThemeDarkIcon, ThemeLightIcon, ThemeSystemIcon, type LucideIcon } from './icons';
import { cx } from './ui';

const OPTIONS: Array<{ mode: ThemeMode; icon: LucideIcon; title: string }> = [
  { mode: 'light', icon: ThemeLightIcon, title: '밝은 테마로 고정' },
  { mode: 'dark', icon: ThemeDarkIcon, title: '어두운 테마로 고정' },
  { mode: 'system', icon: ThemeSystemIcon, title: '운영체제 설정을 따라감' },
];

export function ThemeToggle({ className }: { className?: string }) {
  const mode = useThemeMode();

  return (
    <div
      role="radiogroup"
      aria-label="테마"
      className={cx(
        'inline-flex items-center gap-0.5 rounded-lg border border-line bg-surface-2 p-0.5',
        className,
      )}
    >
      {OPTIONS.map((option) => {
        const active = mode === option.mode;
        const Icon = option.icon;
        return (
          <button
            key={option.mode}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={`테마 ${THEME_LABEL[option.mode]}`}
            title={option.title}
            onClick={() => setThemeMode(option.mode)}
            className={cx(
              'inline-flex size-7 items-center justify-center rounded-md transition-colors',
              active
                ? 'bg-surface text-ink shadow-sm shadow-slate-900/10'
                : 'text-muted hover:text-ink',
            )}
          >
            <Icon aria-hidden className="size-4" />
          </button>
        );
      })}
    </div>
  );
}
