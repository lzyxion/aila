import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router';

import { USE_MOCK } from '../api/client';
import type { UserRole } from '../api/types';
import { useAuth, type AuthStatus } from '../auth/AuthContext';
import {
  AdminIcon,
  CloseIcon,
  DashboardIcon,
  ErrorGroupIcon,
  LogoutIcon,
  MenuIcon,
  PolicyIcon,
  type LucideIcon,
} from './icons';
import { RoleBadge } from './StatusBadges';
import { ThemeToggle } from './ThemeToggle';
import { Badge, Button, InfoTip, cx } from './ui';

/**
 * 네비게이션은 **좌측 사이드바**이고 두 덩어리다.
 *
 * 일반 영역은 "지금 무엇이 터지고 있나"를 보는 자리(대시보드·분석 정책·오류 그룹)이고,
 * 관리 영역(`/admin`)은 설정·계정·비용처럼 **자주 열지 않지만 위험한** 것들이다. 둘을
 * 한 줄에 섞어 두면 매일 쓰는 화면과 한 달에 한 번 여는 화면이 같은 무게로 보인다.
 *
 * 상단 바에서 사이드바로 옮긴 이유: 항목이 늘어날 자리가 필요하고(관리 하위 5개),
 * 세로 목록은 **섹션 제목**을 달 수 있어 두 덩어리의 구분이 구분선 하나보다 분명하다.
 * lg 미만에서는 세로 목록이 본문 폭을 먹으므로 상단 바 + 드로어로 접는다.
 *
 * 관리 영역은 admin 전용이라 viewer 에게는 메뉴 자체를 감춘다 — 다만 그건 편의이고,
 * 주소를 직접 쳐서 들어와도 라우트 가드가 안내로 막고 최종 판정은 서버가 한다.
 */
interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  title?: string;
}

const MAIN_NAV: NavItem[] = [
  { to: '/', label: '대시보드', icon: DashboardIcon, end: true },
  { to: '/policies', label: '분석 정책', icon: PolicyIcon },
  { to: '/error-groups', label: '오류 그룹', icon: ErrorGroupIcon, end: true },
];

const ADMIN_NAV: NavItem[] = [
  {
    to: '/admin',
    label: '관리',
    icon: AdminIcon,
    title: 'LLM 연결·Loki 연결·분석 이력·사용량·사용자 관리 — admin 전용입니다.',
  },
];

export function Layout() {
  const { status, user, logout, logoutPending } = useAuth();
  const location = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // 인증 미배포 백엔드에는 역할 자체가 없다 — 예전처럼 전부 열어 둔다.
  const showAdmin = status === 'disabled' || user?.role === 'admin';

  // 이동하면 드로어는 닫는다 — 열린 채로 두면 방금 연 화면이 가려진다.
  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname, location.search]);

  useEffect(() => {
    if (!drawerOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setDrawerOpen(false);
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [drawerOpen]);

  const sidebar = (
    <SidebarContent
      showAdmin={showAdmin}
      status={status}
      username={user?.username}
      role={user?.role}
      logout={logout}
      logoutPending={logoutPending}
    />
  );

  return (
    <div className="min-h-screen bg-canvas">
      {/* 키보드 사용자가 매 화면마다 네비 12개를 지나치지 않게. */}
      <a
        href="#aila-main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-ink focus:shadow-lg"
      >
        본문으로 건너뛰기
      </a>

      {/* --------------------------------------------------- lg 이상: 고정 사이드바 */}
      <div className="hidden lg:fixed lg:inset-y-0 lg:left-0 lg:flex lg:w-64 lg:flex-col lg:border-r lg:border-line lg:bg-surface">
        {sidebar}
      </div>

      {/* --------------------------------------------------- lg 미만: 상단 바 + 드로어 */}
      <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-surface/90 px-4 py-2.5 backdrop-blur lg:hidden">
        <Button
          variant="ghost"
          size="sm"
          aria-label="메뉴 열기"
          aria-expanded={drawerOpen}
          aria-controls={drawerOpen ? 'aila-drawer' : undefined}
          onClick={() => setDrawerOpen(true)}
        >
          <MenuIcon aria-hidden className="size-5" />
        </Button>
        <NavLink to="/" className="flex items-baseline gap-2">
          <span className="text-base font-bold tracking-tight text-ink">AILA</span>
        </NavLink>
        <div className="ml-auto flex items-center gap-2">
          {USE_MOCK && <MockBadge />}
          <ThemeToggle />
        </div>
      </header>

      {drawerOpen && (
        <div className="lg:hidden">
          <button
            type="button"
            aria-label="메뉴 닫기"
            className="fixed inset-0 z-40 bg-overlay"
            onClick={() => setDrawerOpen(false)}
          />
          <div
            id="aila-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="주 메뉴"
            className="fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col border-r border-line bg-surface shadow-2xl"
          >
            <div className="flex justify-end px-2 pt-2">
              <Button variant="ghost" size="sm" aria-label="메뉴 닫기" onClick={() => setDrawerOpen(false)}>
                <CloseIcon aria-hidden className="size-5" />
              </Button>
            </div>
            {sidebar}
          </div>
        </div>
      )}

      {/* ------------------------------------------------------------------ 본문 */}
      <div className="lg:pl-64">
        <main id="aila-main" className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
          <Outlet />
        </main>

        <footer className="mx-auto max-w-7xl px-4 pb-10 text-xs leading-relaxed text-muted sm:px-6">
          {/*
            고지는 지우지 않고 **접는다.** 한 줄에 결론만 남기고, 왜 그런지·무엇이 예외인지는
            ⓘ 안에 그대로 둔다 — 매 화면 하단에 세 줄이 깔리면 아무도 읽지 않는다.
          */}
          <p className="flex flex-wrap items-center gap-1.5">
            <span>
              화면의 로그는 전부 <strong>마스킹된 값</strong>이고, LLM 분석 결과는{' '}
              <strong>가설</strong>·비용은 <strong>추정</strong>입니다.
            </span>
            <InfoTip label="마스킹·LLM 고지 자세히 보기" title="이 화면이 보여주는 것" side="top">
              마스킹 전 원본 로그는 <strong>저장하지 않습니다</strong> — 필요하면 그룹의 라벨·시각으로
              Loki 에서 다시 조회합니다.
              <span className="mt-1.5 block">
                LLM 이 낸 원인·심각도는 대표 로그 몇 건에서 나온 <strong>가설</strong>이고, 토큰
                단가가 등록되지 않은 구간의 비용은 0 이 아니라 <strong>알 수 없음(-)</strong> 입니다.
              </span>
              <span className="mt-1.5 block">
                {status === 'disabled'
                  ? '이 백엔드에는 인증이 없으므로 로컬·데모 환경에서만 사용하십시오.'
                  : '권한 판정은 서버가 합니다 — 화면의 버튼 상태는 편의일 뿐입니다.'}
              </span>
            </InfoTip>
          </p>
        </footer>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------- 사이드바 본체

function SidebarContent({
  showAdmin,
  status,
  username,
  role,
  logout,
  logoutPending,
}: {
  showAdmin: boolean;
  status: AuthStatus;
  username?: string;
  role?: UserRole;
  logout: () => void;
  logoutPending: boolean;
}) {
  return (
    <>
      <div className="px-5 pt-5 pb-4">
        <NavLink to="/" className="block">
          <span className="block text-lg font-bold tracking-tight text-ink">AILA</span>
          <span className="mt-0.5 block text-xs text-muted">Loki 기반 AI 로그 분석기</span>
        </NavLink>
      </div>

      <nav className="aila-scroll flex-1 overflow-y-auto px-3 pb-4" aria-label="주 메뉴">
        <NavSection label="모니터링" items={MAIN_NAV} />
        {showAdmin && <NavSection label="관리" items={ADMIN_NAV} className="mt-5" />}
      </nav>

      <div className="mt-auto space-y-3 border-t border-line px-4 py-4">
        <div className="flex flex-wrap items-center gap-1.5">
          {USE_MOCK && <MockBadge />}
          {/*
            인증 미배포(백엔드에 /api/auth 가 없음)는 실패가 아니라 폴백 상태다.
            그렇다고 조용히 넘어가면 "로그인 없이 쓰는 중"이라는 사실이 화면에서 사라진다.
          */}
          {status === 'disabled' && (
            <Badge
              tone="neutral"
              title="백엔드에 /api/auth 경로가 아직 없습니다. 인증 없이 동작하는 중이며 로컬·데모 환경 전용입니다."
            >
              인증 미배포
            </Badge>
          )}
        </div>

        {username && (
          <div className="space-y-1.5">
            <p className="truncate text-sm font-medium text-ink">{username}</p>
            {role && <RoleBadge role={role} />}
          </div>
        )}

        <div className="flex items-center justify-between gap-2">
          <ThemeToggle />
          {username && (
            <Button size="sm" variant="ghost" disabled={logoutPending} onClick={logout}>
              <LogoutIcon aria-hidden className="size-4" />
              {logoutPending ? '로그아웃 중…' : '로그아웃'}
            </Button>
          )}
        </div>
      </div>
    </>
  );
}

function NavSection({
  label,
  items,
  className,
}: {
  label: string;
  items: NavItem[];
  className?: string;
}) {
  return (
    <div className={className}>
      <p className="px-2 pb-1.5 text-[0.6875rem] font-semibold tracking-wider text-faint uppercase">
        {label}
      </p>
      <ul className="space-y-0.5">
        {items.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.end}
              title={item.title}
              className={({ isActive }) =>
                cx(
                  'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-nav-active text-nav-active-fg'
                    : 'text-ink-soft hover:bg-surface-3 hover:text-ink',
                )
              }
            >
              <item.icon aria-hidden className="size-4 shrink-0" />
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MockBadge() {
  return (
    <Badge tone="warning" title="VITE_USE_MOCK=true — 백엔드에 붙지 않고 fixture 데이터를 보여줍니다.">
      MOCK 데이터
    </Badge>
  );
}
