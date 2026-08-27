import { Navigate, Route, Routes, useLocation } from 'react-router';
import type { ReactNode } from 'react';

import { useAuth } from './auth/AuthContext';
import { Layout } from './components/Layout';
import { LoadingBlock } from './components/ui';
import { AdminLayout } from './pages/AdminPage';
import { AnalysisJobsPage } from './pages/AnalysisJobsPage';
import { DashboardPage } from './pages/DashboardPage';
import { ErrorGroupDetailPage } from './pages/ErrorGroupDetailPage';
import { ErrorGroupsPage } from './pages/ErrorGroupsPage';
import { HomePage } from './pages/HomePage';
import { LlmConnectionsPage } from './pages/LlmConnectionsPage';
import { LoginPage } from './pages/LoginPage';
import { LogSourceConnectionsPage } from './pages/LogSourceConnectionsPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { PoliciesPage } from './pages/PoliciesPage';
import { PolicyEditPage } from './pages/PolicyEditPage';
import { QueryRunPage } from './pages/QueryRunPage';
import { UsagePage } from './pages/UsagePage';
import { UsersPage } from './pages/UsersPage';

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
        {/* ---------------------------------------------------------- 일반 영역 */}
        {/* 홈은 요약 카드 + 정책 카드 그리드 + 전체 오류 그룹이다. */}
        <Route index element={<HomePage />} />
        {/* 정책별 상세 대시보드. 카드의 "대시보드"에서 들어온다. */}
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="dashboard/:policyId" element={<DashboardPage />} />

        {/*
          정책은 목록(조회 전용)과 편집 페이지가 분리돼 있다 — 폼이 목록 옆에 있으면
          "지금 무엇을 고치는 중인가"가 스크롤 밖으로 밀린다.
          `new` 를 `:policyId/edit` 보다 **위에** 둘 필요는 없다(경로 모양이 다르다).
        */}
        <Route path="policies" element={<PoliciesPage />} />
        <Route path="policies/new" element={<PolicyEditPage />} />
        <Route path="policies/:policyId/edit" element={<PolicyEditPage />} />

        {/* 전 정책의 오류 그룹 한 목록 + 그룹 상세. */}
        <Route path="error-groups" element={<ErrorGroupsPage />} />
        <Route path="error-groups/:groupId" element={<ErrorGroupDetailPage />} />

        {/* 정책 실행 이력 → 그 회차의 오류 그룹 목록 */}
        <Route path="query-runs/:runId" element={<QueryRunPage />} />

        {/* ---------------------------------------------------------- 관리 영역 */}
        <Route path="admin" element={<AdminLayout />}>
          <Route index element={<Navigate to="/admin/llm-connections" replace />} />
          <Route path="llm-connections" element={<LlmConnectionsPage />} />
          {/* 로그 소스 연결 — 수집 확인 대상(expected_services)을 여기서 정한다. */}
          <Route path="log-source-connections" element={<LogSourceConnectionsPage />} />
          <Route path="analysis-jobs" element={<AnalysisJobsPage />} />
          <Route path="usage" element={<UsagePage />} />
          <Route path="users" element={<UsersPage />} />
        </Route>

        {/*
          예전 경로. 북마크·문서·이전 화면의 링크가 살아 있으므로 404 로 떨어뜨리지 않고
          관리 영역의 새 자리로 보낸다 (`replace` — 뒤로가기가 리다이렉트 루프가 되지 않게).
        */}
        <Route path="usage" element={<Navigate to="/admin/usage" replace />} />
        <Route path="llm-connections" element={<Navigate to="/admin/llm-connections" replace />} />

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
    // 세션 확인은 앱 전체를 막는 단계다 — 카드 안이 아니라 화면 한가운데에 둔다.
    return (
      <div className="grid min-h-screen place-items-center bg-canvas">
        <LoadingBlock label="세션을 확인하는 중…" />
      </div>
    );
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
