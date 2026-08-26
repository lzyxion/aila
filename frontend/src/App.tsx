import { Route, Routes } from 'react-router';

import { Layout } from './components/Layout';
import { DashboardPage } from './pages/DashboardPage';
import { ErrorGroupDetailPage } from './pages/ErrorGroupDetailPage';
import { LlmConnectionsPage } from './pages/LlmConnectionsPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { PoliciesPage } from './pages/PoliciesPage';
import { QueryRunPage } from './pages/QueryRunPage';
import { UsagePage } from './pages/UsagePage';

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
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
