import { NavLink, Outlet } from 'react-router';

import { USE_MOCK } from '../api/client';
import { cx } from './ui';

const NAV = [
  { to: '/', label: '대시보드', end: true },
  { to: '/policies', label: '분석 정책' },
  { to: '/llm-connections', label: 'LLM 연결' },
  { to: '/usage', label: '분석 이력·사용량' },
];

export function Layout() {
  return (
    <div className="min-h-screen bg-slate-100">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-6 py-3">
          <NavLink to="/" className="flex items-baseline gap-2">
            <span className="text-lg font-bold tracking-tight text-slate-900">AILA</span>
            <span className="hidden text-xs text-slate-500 sm:inline">
              Loki 기반 AI 로그 분석기
            </span>
          </NavLink>

          <nav className="flex flex-1 flex-wrap items-center gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cx(
                    'rounded-lg px-3 py-1.5 text-sm font-medium transition-colors',
                    isActive
                      ? 'bg-slate-900 text-white'
                      : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          {USE_MOCK && (
            <span
              className="rounded-md bg-amber-100 px-2 py-1 text-xs font-semibold text-amber-900 ring-1 ring-amber-200 ring-inset"
              title="VITE_USE_MOCK=true — 백엔드에 붙지 않고 fixture 데이터를 보여줍니다."
            >
              MOCK 데이터
            </span>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        <Outlet />
      </main>

      <footer className="mx-auto max-w-7xl px-6 pb-10 text-xs leading-relaxed text-slate-500">
        <p>
          화면에 표시되는 로그는 전부 <strong>마스킹된 값</strong>입니다. 마스킹 전 원본은 저장하지
          않으며, 필요하면 그룹의 라벨·시각으로 Loki 에서 재조회합니다.
        </p>
        <p className="mt-1">
          LLM 분석 결과는 <strong>원인 가설</strong>이고 비용은 <strong>추정</strong>값입니다.
          MVP 에는 인증이 없으므로 로컬·데모 환경에서만 사용하십시오.
        </p>
      </footer>
    </div>
  );
}
