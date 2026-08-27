/**
 * 화면 전반에서 쓰는 표시 원시 요소. 디자인 토큰을 여기 한 곳에 모아 둔다.
 *
 * **색은 의미 토큰으로만 쓴다** (`bg-surface`·`text-muted`·`border-line`). 원색
 * (`bg-white`·`text-slate-500`)을 쓰면 다크 모드가 클래스마다 `dark:` 를 하나씩 더 다는
 * 일이 되고, 한 군데를 빠뜨리면 검은 화면에 흰 카드가 남는다. 토큰 정의는 `index.css`.
 *
 * 예외는 **색조(tone)** 하나다 — info/warning/danger 는 뜻이 색에 붙어 있어 토큰으로
 * 뽑으면 이름이 6배로 늘어난다. 대신 `toneStyles` 한 곳에서만 `dark:` 짝을 맞춘다.
 */

import { useId, useState, type ReactNode } from 'react';
import { Link, type LinkProps } from 'react-router';

import { ApiError, isNotImplemented } from '../api/client';
import { InfoGlyph, RunIcon, type LucideIcon } from './icons';

export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ');
}

// -------------------------------------------------------------------- 레이아웃

export function Card({
  title,
  description,
  info,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  description?: ReactNode;
  /**
   * 제목 옆 ⓘ 로 들어가는 **정확성 문구**. 카드 머리말은 한 줄로 두고, 계약 설명
   * ("count_over_time 기준이라 로그 라인 수가 아닙니다" 같은)은 여기로 옮긴다.
   * 지우는 게 아니라 **옮기는** 것이다.
   */
  info?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cx(
        'rounded-xl border border-line bg-surface shadow-sm shadow-slate-900/5',
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            {title && (
              <div className="flex items-center gap-1.5">
                <h2 className="text-base font-semibold text-ink">{title}</h2>
                {info && <InfoTip title={typeof title === 'string' ? title : undefined}>{info}</InfoTip>}
              </div>
            )}
            {description && <p className="mt-1 text-sm text-muted">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

export function PageHeader({
  title,
  description,
  info,
  actions,
}: {
  title: string;
  /** **한 줄**로 끝낸다. 두 줄이 필요하면 둘째 줄은 `info` 로 내려보낸다. */
  description?: ReactNode;
  info?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <h1 className="text-2xl font-bold tracking-tight text-ink">{title}</h1>
          {info && <InfoTip title={title}>{info}</InfoTip>}
        </div>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/** 카드 사이의 표준 세로 간격. 페이지 최상위 컨테이너에 쓴다. */
export function PageStack({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('space-y-6', className)}>{children}</div>;
}

// ---------------------------------------------------------------------- 버튼

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

const buttonStyles: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-accent-fg shadow-sm shadow-sky-900/20 hover:bg-accent-hover disabled:bg-surface-3 disabled:text-faint disabled:shadow-none',
  secondary:
    'border border-line-strong bg-surface text-ink-soft hover:bg-surface-2 disabled:text-faint',
  ghost: 'text-muted hover:bg-surface-3 hover:text-ink disabled:text-faint',
  danger:
    'border border-rose-200 bg-surface text-rose-700 hover:bg-rose-50 disabled:text-faint dark:border-rose-900 dark:text-rose-300 dark:hover:bg-rose-950',
};

const buttonSizes = {
  sm: 'px-2.5 py-1.5 text-xs',
  md: 'px-3.5 py-2 text-sm',
  /** 화면에서 "여기를 누르는 화면"임을 알려야 하는 주 동작에만 쓴다 (대시보드 정책 실행). */
  lg: 'px-5 py-2.5 text-sm font-semibold',
};

const buttonBase =
  'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed';

export function Button({
  variant = 'secondary',
  size = 'md',
  className,
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: keyof typeof buttonSizes;
}) {
  return (
    <button
      type="button"
      className={cx(buttonBase, buttonSizes[size], buttonStyles[variant], className)}
      {...rest}
    >
      {children}
    </button>
  );
}

/**
 * 버튼 모양의 링크 (react-router `Link`).
 *
 * "누르면 이동한다"는 **링크**이지 버튼이 아니다 — 가운데 클릭·새 탭·주소 복사가 되어야
 * 하고, 스크린 리더도 "링크"라고 읽어야 한다. 그런데 화면에서는 버튼처럼 보여야 하는
 * 자리가 많다(카드 하단의 "대시보드"·"그룹 보기"). 페이지마다 손으로 만든 버튼 모양
 * 링크가 조금씩 다른 색을 갖는 것을 막으려고 `Button` 과 **같은 변형·크기**를 쓴다.
 */
export function ButtonLink({
  variant = 'secondary',
  size = 'md',
  className,
  children,
  ...rest
}: LinkProps & {
  variant?: ButtonVariant;
  size?: keyof typeof buttonSizes;
}) {
  return (
    <Link
      className={cx(buttonBase, buttonSizes[size], buttonStyles[variant], className)}
      {...rest}
    >
      {children}
    </Link>
  );
}

/** 본문 안의 인라인 링크. 밑줄은 유지한다 — 색만으로 링크임을 알리지 않는다. */
export function TextLink({ className, children, ...rest }: LinkProps) {
  return (
    <Link
      className={cx(
        'font-medium text-accent-ink underline underline-offset-2 hover:no-underline',
        className,
      )}
      {...rest}
    >
      {children}
    </Link>
  );
}

// ---------------------------------------------------------------------- 아이콘

/**
 * 실행(▶). 대시보드의 주 동작 버튼이 글자만으로 묻히지 않게 붙인다.
 * (기존 호출부 호환용 이름 — 새 코드는 `icons.ts` 의 `RunIcon` 을 그대로 써도 된다.)
 */
export function PlayIcon({ className }: { className?: string }) {
  return <RunIcon aria-hidden className={cx('size-4 shrink-0', className)} />;
}

/** 정보 표시. 옆에 설명을 붙일 자리를 시각적으로 알린다. */
export function InfoIcon({ className }: { className?: string }) {
  return <InfoGlyph aria-hidden className={cx('size-3.5 shrink-0', className)} />;
}

// ------------------------------------------------------------------- InfoTip

/**
 * ⓘ 버튼 + 팝오버.
 *
 * **밀도 정리에서 설명문이 이주하는 자리다.** 화면 본문에 늘어놓으면 매번 읽히지 않고
 * 정작 숫자를 가리는 계약 문구("count_over_time 기준이라 로그 라인 수가 아닙니다",
 * "null 은 0 이 아닙니다")를 여기로 옮긴다. **지우지 않는다** — 정확성 문구가 화면
 * 어디에도 없으면 숫자를 잘못 읽는 사람이 생기고, 그건 밀도 문제가 아니라 정확성 문제다.
 *
 * 접근성:
 * - hover 만이 아니라 **focus 로도 열린다** (키보드·터치 사용자).
 * - 열린 패널은 `role="tooltip"` 이고 버튼이 `aria-describedby` 로 가리킨다.
 * - Escape 로 닫힌다.
 *
 * 내용은 **phrasing content** 로 쓴다 (`<span className="mt-1 block">` 은 되고 `<p>` 는
 * 안 된다) — 바깥이 `<span>` 이라 문단 요소를 넣으면 유효하지 않은 HTML 이 된다.
 * 여러 문단이 필요할 만큼 길면 그건 툴팁이 아니라 `Notice` 로 남길 내용이다.
 */
export function InfoTip({
  children,
  title,
  label,
  align = 'start',
  side = 'bottom',
  className,
}: {
  children: ReactNode;
  /** 팝오버 안의 굵은 첫 줄. 없으면 본문만 나온다. */
  title?: string;
  /** 버튼의 접근성 이름. 기본은 "설명 보기" — 같은 화면에 여럿이면 구체적으로 준다. */
  label?: string;
  /** 가로 정렬. 표 오른쪽 칸·화면 우측 끝에서는 `end` 로 두어야 잘리지 않는다. */
  align?: 'start' | 'center' | 'end';
  /** 화면 아래쪽(푸터·표의 마지막 행)에서는 `top` 으로 위로 편다. */
  side?: 'bottom' | 'top';
  className?: string;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);

  return (
    <span
      className={cx('relative inline-flex align-middle', className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && open) {
          event.stopPropagation();
          setOpen(false);
        }
      }}
    >
      <button
        type="button"
        aria-label={label ?? (title ? `${title} 설명 보기` : '설명 보기')}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex size-5 items-center justify-center rounded-full text-faint transition-colors hover:bg-surface-3 hover:text-ink-soft"
      >
        <InfoGlyph aria-hidden className="size-3.5" />
      </button>
      {open && (
        <span
          role="tooltip"
          id={id}
          className={cx(
            'absolute z-40 w-72 rounded-lg border border-line bg-surface px-3 py-2 text-left text-xs leading-relaxed font-normal text-ink-soft normal-case shadow-lg shadow-slate-900/10',
            side === 'bottom' ? 'top-full mt-1.5' : 'bottom-full mb-1.5',
            align === 'start' && 'left-0',
            align === 'center' && 'left-1/2 -translate-x-1/2',
            align === 'end' && 'right-0',
          )}
        >
          {title && <span className="mb-1 block font-semibold text-ink">{title}</span>}
          {children}
        </span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------- 폼

export function Field({
  label,
  hint,
  info,
  required,
  children,
  className,
}: {
  label: string;
  hint?: ReactNode;
  /** 긴 제약 설명은 `hint` 대신 여기로 — 폼이 설명문으로 두 배 길어지는 것을 막는다. */
  info?: ReactNode;
  required?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cx('block', className)}>
      <span className="mb-1 flex items-baseline gap-1 text-sm font-medium text-ink-soft">
        {label}
        {required && <span className="text-rose-600 dark:text-rose-400">*</span>}
        {info && (
          <InfoTip label={`${label} 설명 보기`} title={label}>
            {info}
          </InfoTip>
        )}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

const controlClass =
  'w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-faint focus:border-focus focus:ring-0 disabled:bg-surface-2 disabled:text-muted';

export function Input({ className, ...rest }: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx(controlClass, className)} {...rest} />;
}

export function Textarea({
  className,
  ...rest
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx(controlClass, 'font-mono leading-relaxed', className)} {...rest} />;
}

export function Select({
  className,
  children,
  ...rest
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx(controlClass, className)} {...rest}>
      {children}
    </select>
  );
}

export function Checkbox({
  label,
  hint,
  className,
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: ReactNode }) {
  return (
    <label className={cx('flex items-start gap-2.5', className)}>
      <input
        type="checkbox"
        className="mt-0.5 size-4 rounded border-line-strong bg-surface text-accent focus:ring-focus"
        {...rest}
      />
      <span>
        <span className="block text-sm font-medium text-ink-soft">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-muted">{hint}</span>}
      </span>
    </label>
  );
}

// -------------------------------------------------------------------- 배지

export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent';

/**
 * 색조 팔레트. **다크 짝을 여기서만 맞춘다.**
 *
 * 다크에서 950 배경 + 200 글자로 뒤집는다 (밝은 테마의 50 배경 + 800 글자를 그대로
 * 뒤집은 짝이다). 링은 900 — 배경과 한 단계만 벌려 테두리가 글자보다 튀지 않게 한다.
 */
const toneStyles: Record<Tone, string> = {
  neutral:
    'bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-700',
  info: 'bg-sky-50 text-sky-800 ring-sky-200 dark:bg-sky-950 dark:text-sky-200 dark:ring-sky-900',
  success:
    'bg-emerald-50 text-emerald-800 ring-emerald-200 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-900',
  warning:
    'bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-900',
  danger:
    'bg-rose-50 text-rose-800 ring-rose-200 dark:bg-rose-950 dark:text-rose-200 dark:ring-rose-900',
  accent:
    'bg-violet-50 text-violet-800 ring-violet-200 dark:bg-violet-950 dark:text-violet-200 dark:ring-violet-900',
};

/**
 * 색조 클래스를 그대로 꺼내 쓰는 문. 페이지가 `bg-amber-50 text-amber-800` 을 손으로
 * 적으면 다크 짝이 빠진다 — 색조가 필요한 블록은 이 함수를 통해서만 칠한다.
 */
export function toneClass(tone: Tone): string {
  return toneStyles[tone];
}

export function Badge({
  tone = 'neutral',
  children,
  className,
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset',
        toneStyles[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

// ------------------------------------------------------------------ 알림 박스

export function Notice({
  tone = 'info',
  title,
  children,
  className,
}: {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx('rounded-lg px-4 py-3 text-sm ring-1 ring-inset', toneStyles[tone], className)}>
      {title && <p className="font-semibold">{title}</p>}
      {children && <div className={cx(title ? 'mt-1' : null, 'leading-relaxed')}>{children}</div>}
    </div>
  );
}

/**
 * 본문 안의 인라인 코드.
 *
 * 배경을 `bg-black/5 dark:bg-white/10` 로 두는 이유: 이 칩은 흰 카드 위에도, 색조가
 * 칠해진 `Notice` 안에도 들어간다. 불투명 색을 쓰면 알림 상자 안에서 혼자 회색 조각이
 * 되고, 다크에서는 아예 반대로 뒤집힌다. 반투명이면 어느 바탕에서도 한 단계만 어두워진다.
 */
export function Code({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <code
      className={cx(
        'rounded bg-black/5 px-1 py-0.5 font-mono text-[0.9em] dark:bg-white/10',
        className,
      )}
    >
      {children}
    </code>
  );
}

// -------------------------------------------------------------- 상태 표시

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="불러오는 중"
      className={cx(
        'inline-block size-4 animate-spin rounded-full border-2 border-line-strong border-t-accent',
        className,
      )}
    />
  );
}

export function LoadingBlock({ label = '불러오는 중…' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted">
      <Spinner />
      {label}
    </div>
  );
}

// ------------------------------------------------------------------ 스켈레톤

/**
 * 스켈레톤 한 조각.
 *
 * `LoadingBlock`(스피너 한 개)의 상위 대안이다. **화면의 뼈대가 이미 정해져 있을 때만**
 * 쓴다 — 표·통계 타일·카드처럼 몇 줄짜리인지 아는 자리에서는 스켈레톤이 레이아웃
 * 이동을 없애 준다. 반대로 결과 개수를 모르는 자리(검색 결과 등)에 쓰면 "3건 있다"는
 * 거짓 신호를 준다. 그때는 `LoadingBlock` 이 정직하다.
 *
 * 스켈레톤 자체는 장식이라 `aria-hidden` 이고, "불러오는 중"이라는 사실은 감싸는
 * 컨테이너의 `role="status"` 가 글자로 전한다.
 */
export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden className={cx('block animate-pulse rounded-md bg-surface-3', className)} />;
}

/**
 * 스켈레톤 묶음의 껍데기. 아래 프리셋(`SkeletonText`·`SkeletonStats`…)에 맞지 않는
 * 모양을 손으로 조립할 때 쓴다 — `role="status"` 와 sr-only 라벨을 빠뜨리지 않게.
 */
export function SkeletonRegion({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="status" aria-busy="true">
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}

export function SkeletonText({
  lines = 3,
  className,
  label = '불러오는 중',
}: {
  lines?: number;
  className?: string;
  label?: string;
}) {
  return (
    <SkeletonRegion label={label}>
      <div className={cx('space-y-2', className)}>
        {Array.from({ length: lines }, (_, index) => (
          <Skeleton
            key={index}
            className={cx('h-3.5', index === lines - 1 ? 'w-2/3' : 'w-full')}
          />
        ))}
      </div>
    </SkeletonRegion>
  );
}

export function SkeletonStats({ count = 4, label = '요약을 불러오는 중' }: { count?: number; label?: string }) {
  return (
    <SkeletonRegion label={label}>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: count }, (_, index) => (
          <div key={index} className="rounded-lg border border-line bg-surface px-4 py-3">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="mt-2 h-7 w-16" />
            <Skeleton className="mt-1.5 h-3 w-24" />
          </div>
        ))}
      </div>
    </SkeletonRegion>
  );
}

export function SkeletonTable({
  rows = 5,
  cols = 4,
  label = '목록을 불러오는 중',
}: {
  rows?: number;
  cols?: number;
  label?: string;
}) {
  return (
    <SkeletonRegion label={label}>
      <div className="space-y-3">
        <div className="flex gap-3 border-b border-line pb-2">
          {Array.from({ length: cols }, (_, index) => (
            <Skeleton key={index} className="h-3 flex-1" />
          ))}
        </div>
        {Array.from({ length: rows }, (_, rowIndex) => (
          <div key={rowIndex} className="flex gap-3">
            {Array.from({ length: cols }, (_, colIndex) => (
              <Skeleton key={colIndex} className="h-4 flex-1" />
            ))}
          </div>
        ))}
      </div>
    </SkeletonRegion>
  );
}

export function SkeletonCard({ lines = 3, label = '불러오는 중' }: { lines?: number; label?: string }) {
  return (
    <SkeletonRegion label={label}>
      <div className="rounded-xl border border-line bg-surface p-5">
        <Skeleton className="h-4 w-32" />
        <div className="mt-4 space-y-2">
          {Array.from({ length: lines }, (_, index) => (
            <Skeleton key={index} className={cx('h-3.5', index === lines - 1 ? 'w-1/2' : 'w-full')} />
          ))}
        </div>
      </div>
    </SkeletonRegion>
  );
}

/**
 * 빈 상태.
 *
 * `icon` 은 장식이다 — 빈 이유는 **글자로** 적는다. 그림만 두면 "로딩 실패"인지
 * "정말 없음"인지 구분되지 않는다.
 */
export function EmptyBlock({ children, icon: Icon }: { children: ReactNode; icon?: LucideIcon }) {
  return (
    <div className="rounded-lg border border-dashed border-line-strong px-4 py-10 text-center text-sm text-muted">
      {Icon && <Icon aria-hidden className="mx-auto mb-2 size-5 text-faint" />}
      {children}
    </div>
  );
}

/**
 * 오류 표시. 백엔드 스텁(501)은 실패가 아니라 "아직 구현되지 않음"으로 안내한다 —
 * Phase 1 에서는 대부분의 엔드포인트가 여기에 해당한다.
 */
export function ErrorBlock({ error, hint }: { error: unknown; hint?: ReactNode }) {
  if (isNotImplemented(error)) {
    return (
      <Notice tone="warning" title="백엔드가 아직 이 기능을 구현하지 않았습니다 (501)">
        <p>{(error as ApiError).detail}</p>
        <p className="mt-1 text-xs">
          <Code>VITE_USE_MOCK=true</Code> 로 두면 fixture 데이터로 화면을 확인할 수 있습니다.
        </p>
      </Notice>
    );
  }
  const status = error instanceof ApiError ? error.status : null;
  const message =
    error instanceof Error ? error.message : typeof error === 'string' ? error : '알 수 없는 오류';
  return (
    <Notice tone="danger" title={status ? `요청 실패 (HTTP ${status})` : '요청 실패'}>
      <p className="break-words">{message}</p>
      {hint && <p className="mt-1 text-xs">{hint}</p>}
    </Notice>
  );
}

// -------------------------------------------------------------------- 테이블

export function TableWrap({
  children,
  minWidth = '46rem',
}: {
  children: ReactNode;
  /** 이 폭 아래로는 표를 줄이지 않고 컨테이너 안에서 가로 스크롤한다. */
  minWidth?: string;
}) {
  return (
    <div className="aila-scroll -mx-5 overflow-x-auto px-5">
      <table className="w-full border-collapse text-sm" style={{ minWidth }}>
        {children}
      </table>
    </div>
  );
}

export function Th({
  children,
  className,
  align = 'left',
}: {
  children: ReactNode;
  className?: string;
  align?: 'left' | 'right' | 'center';
}) {
  return (
    <th
      scope="col"
      className={cx(
        'border-b border-line px-3 py-2 text-xs font-semibold tracking-wide text-muted uppercase',
        align === 'right' && 'text-right',
        align === 'center' && 'text-center',
        align === 'left' && 'text-left',
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  className,
  align = 'left',
  colSpan,
}: {
  children?: ReactNode;
  className?: string;
  align?: 'left' | 'right' | 'center';
  colSpan?: number;
}) {
  return (
    <td
      colSpan={colSpan}
      className={cx(
        'border-b border-line-soft px-3 py-2.5 align-top text-ink-soft',
        align === 'right' && 'text-right tabular-nums',
        align === 'center' && 'text-center',
        className,
      )}
    >
      {children}
    </td>
  );
}

// ------------------------------------------------------------- 통계 타일

export function Stat({
  label,
  value,
  sub,
  info,
  icon: Icon,
  tone = 'neutral',
}: {
  label: ReactNode;
  value: ReactNode;
  /** **20자 안쪽**의 한 줄. 길어지면 타일이 문단이 되고 숫자가 안 보인다 — `info` 로 옮긴다. */
  sub?: ReactNode;
  /** 값의 정의·단위 같은 계약 문구 ("count_over_time 기준" 등). */
  info?: ReactNode;
  icon?: LucideIcon;
  tone?: 'neutral' | 'accent';
}) {
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <p className="flex items-center gap-1.5 text-xs font-medium text-muted">
        {Icon && <Icon aria-hidden className="size-3.5 shrink-0" />}
        <span className="min-w-0 truncate">{label}</span>
        {info && (
          <InfoTip label={typeof label === 'string' ? `${label} 설명 보기` : undefined}>
            {info}
          </InfoTip>
        )}
      </p>
      <p
        className={cx(
          'mt-1 text-2xl font-semibold tabular-nums',
          tone === 'accent' ? 'text-accent-ink' : 'text-ink',
        )}
      >
        {value}
      </p>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </div>
  );
}

/**
 * 한 줄에 고정되는 코드 표시. 넘치면 줄바꿈이 아니라 **말줄임**이다.
 *
 * 마스킹된 API 키처럼 "길이가 얼마든 한 줄이어야 하는" 값에 쓴다 — `break-all` 로 두면
 * 키 하나가 카드 높이를 밀어내고 폼이 스크롤된다 (Phase 4 피드백 5번).
 */
export function OneLineCode({
  children,
  title,
  className,
}: {
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <code
      title={title}
      className={cx(
        // max-w-* 는 호출부가 정한다 — 여기에 max-w-full 을 두면 생성 순서에 따라
        // 호출부의 좁은 상한을 덮어써서 긴 값이 표를 밀어낸다.
        'block min-w-0 truncate rounded bg-surface-3 px-1.5 py-0.5 align-middle font-mono text-xs text-ink-soft',
        className,
      )}
    >
      {children}
    </code>
  );
}

/**
 * 로그 라인 표시 블록. 여기 들어오는 값은 이미 마스킹된 것뿐이다.
 *
 * 두 테마 모두 어둡다 — 로그는 터미널에서 오는 것이고, 밝은 테마에서 흰 바탕에 두면
 * 본문 텍스트와 구분이 사라진다. 다크에서는 카드보다 **더 어둡게** 눌러 면을 구분한다.
 */
export function LogLine({ children }: { children: ReactNode }) {
  return (
    <pre className="aila-scroll overflow-x-auto rounded-lg border border-line bg-log-bg px-3.5 py-3 text-xs leading-relaxed whitespace-pre text-log-ink">
      {children}
    </pre>
  );
}
