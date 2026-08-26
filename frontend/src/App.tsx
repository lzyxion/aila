import { Navigate, Route, Routes, useLocation } from 'react-router';
import type { ReactNode } from 'react';

import { useAuth } from './auth/AuthContext';
import { Layout } from './components/Layout';
import { LoadingBlock } from './components/ui';
import { DashboardPage } from './pages/DashboardPage';
import { ErrorGroupDetailPage } from './pages/ErrorGroupDetailPage';
import { HomePage } from './pages/HomePage';
import { LlmConnectionsPage } from './pages/LlmConnectionsPage';
import { LoginPage } from './pages/LoginPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { PoliciesPage } from './pages/PoliciesPage';
import { QueryRunPage } from './pages/QueryRunPage';
import { UsagePage } from './pages/UsagePage';

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        {/* 홈은 정책 카드 그리드다 — 정책이 많아도 "지금 무엇을 봐야 하는가"를 먼저 답한다. */}
        <Route index element={<HomePage />} />
        {/* 정책별 상세 대시보드. 카드의 "대시보드"에서 들어온다. */}
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="dashboard/:policyId" element={<DashboardPage />} />
        <Route path="policies" element={<PoliciesPage />} />
        <Route path="llm-connections" element={<LlmConnectionsPage />} />
        {/* 정책 실행 이력 → 그 회차의 오류 그룹 목록 */}
        <Route path="query-runs/:runId" element={<QueryRunPage />} />
        <Route path="error-groups/:groupId" element={<ErrorGroupDetailPage />} />
        <Route path="usage" element={<UsagePage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

/**
 * 인증 게이트.
 *
 * `disabled`(백엔드에 auth 라우트가 없음)는 **통과시킨다** — 인증 미배포 백엔드에서
 * 앱 전체를 로그인 화면에 가두면 기존 데모가 통째로 멈춘다. 그 사실은 헤더 배지로 알린다.
 */
function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === 'loading') {
    return <LoadingBlock label="세션을 확인하는 중…" />;
  }
  if (status === 'anonymous') {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: `${location.pathname}${location.search}` }}
      />
    );
  }
  return <>{children}</>;
}
