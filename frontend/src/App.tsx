import { Route, Routes } from 'react-router';

import { Layout } from './components/Layout';
import { DashboardPage } from './pages/DashboardPage';
import { ErrorGroupDetailPage } from './pages/ErrorGroupDetailPage';
import { LlmConnectionsPage } from './pages/LlmConnectionsPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { PoliciesPage } from './pages/PoliciesPage';
import { UsagePage } from './pages/UsagePage';

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="policies" element={<PoliciesPage />} />
        <Route path="llm-connections" element={<LlmConnectionsPage />} />
        <Route path="error-groups/:groupId" element={<ErrorGroupDetailPage />} />
        <Route path="usage" element={<UsagePage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
