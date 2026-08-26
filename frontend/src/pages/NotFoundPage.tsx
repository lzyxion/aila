import { Link } from 'react-router';

import { PageHeader } from '../components/ui';

export function NotFoundPage() {
  return (
    <div>
      <PageHeader title="페이지를 찾을 수 없습니다" />
      <Link to="/" className="text-sm font-medium text-sky-700 hover:underline">
        대시보드로 돌아가기
      </Link>
    </div>
  );
}
