/** 화면 전반에서 쓰는 표시 원시 요소. 디자인 토큰을 여기 한 곳에 모아 둔다. */

import type { ReactNode } from 'react';

import { ApiError, isNotImplemented } from '../api/client';

export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ');
}

// -------------------------------------------------------------------- 레이아웃

export function Card({
  title,
  description,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cx(
        'rounded-xl border border-slate-200 bg-white shadow-sm shadow-slate-200/50',
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
          <div className="min-w-0">
            {title && <h2 className="text-base font-semibold text-slate-900">{title}</h2>}
            {description && <p className="mt-1 text-sm text-slate-500">{description}</p>}
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
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-slate-600">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------- 버튼

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

const buttonStyles: Record<ButtonVariant, string> = {
  primary: 'bg-sky-700 text-white hover:bg-sky-800 disabled:bg-slate-300',
  secondary:
    'border border-slate-300 bg-white text-slate-800 hover:bg-slate-50 disabled:text-slate-400',
  ghost: 'text-slate-600 hover:bg-slate-100 hover:text-slate-900 disabled:text-slate-300',
  danger: 'border border-rose-200 bg-white text-rose-700 hover:bg-rose-50 disabled:text-slate-400',
};

export function Button({
  variant = 'secondary',
  size = 'md',
  className,
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: 'sm' | 'md';
}) {
  return (
    <button
      type="button"
      className={cx(
        'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed',
        size === 'sm' ? 'px-2.5 py-1.5 text-xs' : 'px-3.5 py-2 text-sm',
        buttonStyles[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------- 폼

export function Field({
  label,
  hint,
  required,
  children,
  className,
}: {
  label: string;
  hint?: ReactNode;
  required?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cx('block', className)}>
      <span className="mb-1 flex items-baseline gap-1 text-sm font-medium text-slate-700">
        {label}
        {required && <span className="text-rose-600">*</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

const controlClass =
  'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-sky-600 focus:ring-0 disabled:bg-slate-50 disabled:text-slate-500';

export function Input({
  className,
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement>) {
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
        className="mt-0.5 size-4 rounded border-slate-300 text-sky-700 focus:ring-sky-600"
        {...rest}
      />
      <span>
        <span className="block text-sm font-medium text-slate-700">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-slate-500">{hint}</span>}
      </span>
    </label>
  );
}

// -------------------------------------------------------------------- 배지

type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'accent';

const toneStyles: Record<Tone, string> = {
  neutral: 'bg-slate-100 text-slate-700 ring-slate-200',
  info: 'bg-sky-50 text-sky-800 ring-sky-200',
  success: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  warning: 'bg-amber-50 text-amber-800 ring-amber-200',
  danger: 'bg-rose-50 text-rose-800 ring-rose-200',
  accent: 'bg-violet-50 text-violet-800 ring-violet-200',
};

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
    <div
      className={cx(
        'rounded-lg px-4 py-3 text-sm ring-1 ring-inset',
        toneStyles[tone],
        className,
      )}
    >
      {title && <p className="font-semibold">{title}</p>}
      {children && <div className={cx(title ? 'mt-1' : null, 'leading-relaxed')}>{children}</div>}
    </div>
  );
}

// -------------------------------------------------------------- 상태 표시

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="불러오는 중"
      className={cx(
        'inline-block size-4 animate-spin rounded-full border-2 border-slate-300 border-t-sky-700',
        className,
      )}
    />
  );
}

export function LoadingBlock({ label = '불러오는 중…' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
      <Spinner />
      {label}
    </div>
  );
}

export function EmptyBlock({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 px-4 py-10 text-center text-sm text-slate-500">
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
          <code className="rounded bg-white/60 px-1">VITE_USE_MOCK=true</code> 로 두면 fixture
          데이터로 화면을 확인할 수 있습니다.
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
        'border-b border-slate-200 px-3 py-2 text-xs font-semibold tracking-wide text-slate-500 uppercase',
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
}: {
  children: ReactNode;
  className?: string;
  align?: 'left' | 'right' | 'center';
}) {
  return (
    <td
      className={cx(
        'border-b border-slate-100 px-3 py-2.5 align-top text-slate-700',
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
  tone = 'neutral',
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: 'neutral' | 'accent';
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p
        className={cx(
          'mt-1 text-2xl font-semibold tabular-nums',
          tone === 'accent' ? 'text-sky-800' : 'text-slate-900',
        )}
      >
        {value}
      </p>
      {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

/** 로그 라인 표시 블록. 여기 들어오는 값은 이미 마스킹된 것뿐이다. */
export function LogLine({ children }: { children: ReactNode }) {
  return (
    <pre className="aila-scroll overflow-x-auto rounded-lg bg-slate-900 px-3.5 py-3 text-xs leading-relaxed whitespace-pre text-slate-100">
      {children}
    </pre>
  );
}
