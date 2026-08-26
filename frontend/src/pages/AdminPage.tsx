/**
 * 관리 영역(`/admin`)의 껍데기 — 탭 + 권한 가드.
 *
 * 여기 모인 네 화면(LLM 연결·분석 이력·사용량·사용자 관리)의 공통점은 "매일 보는 화면이
 * 아니고, 잘못 누르면 비용이나 접근 권한이 움직인다"는 것이다. 일반 영역과 섞어 두면
 * 매일 쓰는 대시보드와 한 달에 한 번 여는 계정 관리가 같은 무게로 보인다.
 *
 * **가드는 편의이지 방어선이 아니다.** viewer 가 주소를 직접 쳐서 들어오면 여기서 사유를
 * 적은 안내로 막지만, 진짜 판정은 서버가 한다 (계정 관리 GET 은 403, 그 밖의 쓰기는 403).
 * 그래서 안내 문구도 "권한이 없다"가 아니라 "무엇을 볼 수 있는가"를 함께 적는다.
 */

import { NavLink, Outlet } from 'react-router';
import { Link } from 'react-router';

import { useAuth } from '../auth/AuthContext';
import { RoleBadge } from '../components/StatusBadges';
import { Card, Notice, PageHeader, cx } from '../components/ui';

const TABS = [
  {
    to: '/admin/llm-connections',
    label: 'LLM 연결',
    hint: '분석에 쓸 프로바이더·모델·API 키',
  },
  {
    to: '/admin/analysis-jobs',
    label: '분석 이력',
    hint: '실행된 분석 검색 · 기간 필터',
  },
  { to: '/admin/usage', label: '사용량', hint: '토큰·추정 비용 분해' },
  { to: '/admin/users', label: '사용자 관리', hint: '계정 · 역할 · 비밀번호' },
];

export function AdminLayout() {
  const { status, user } = useAuth();
  // 인증 미배포 백엔드에는 역할이 없다 — 예전처럼 전부 열어 둔다(그 사실은 헤더 배지가 알린다).
  const allowed = status === 'disabled' || user?.role === 'admin';

  if (!allowed) {
    return <AdminDenied />;
  }

  return (
    <div>
      <PageHeader
        title="관리"
        description={
          <>
            연결 설정·비용·계정처럼 <strong>자주 열지 않지만 되돌리기 어려운</strong> 것들을 모아
            둔 영역입니다. 전부 <strong>admin 전용</strong>이고, 화면이 감추는 것과 무관하게
            최종 판정은 서버가 합니다.
          </>
        }
        actions={user ? <RoleBadge role={user.role} /> : undefined}
      />

      <nav
        aria-label="관리 메뉴"
        className="mb-6 flex flex-wrap gap-1 rounded-xl border border-slate-200 bg-white p-1.5 shadow-sm shadow-slate-200/50"
      >
        {TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            title={tab.hint}
            className={({ isActive }) =>
              cx(
                'rounded-lg px-3.5 py-2 text-sm font-medium transition-colors',
                isActive
                  ? 'bg-slate-900 text-white'
                  : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
              )
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>

      <Outlet />
    </div>
  );
}

/**
 * viewer 가 관리 영역 주소로 직접 들어왔을 때.
 *
 * 빈 화면이나 무음 리다이렉트로 처리하지 않는다 — "왜 안 되는가"와 "대신 어디로 가면
 * 되는가"가 없으면 사용자는 같은 주소를 계속 다시 친다.
 */
function AdminDenied() {
  const { user } = useAuth();

  return (
    <div>
      <PageHeader title="관리" description="이 영역은 admin 계정만 사용할 수 있습니다." />
      <Card title="권한 없음">
        <Notice tone="neutral" title="읽기 전용(viewer) 계정입니다">
          <p>
            관리 영역의 네 화면(LLM 연결·분석 이력·사용량·사용자 관리)은 연결 설정·비용·계정을
            다루므로 <strong>admin 계정</strong>만 열 수 있습니다. 지금 계정은{' '}
            <strong>{user?.username ?? '알 수 없음'}</strong> 이고 역할은 <strong>viewer</strong>{' '}
            입니다.
          </p>
          <p className="mt-2">
            조회는 그대로 할 수 있습니다 —{' '}
            <Link to="/" className="font-medium text-sky-800 underline">
              통합 대시보드
            </Link>
            ,{' '}
            <Link to="/policies" className="font-medium text-sky-800 underline">
              분석 정책
            </Link>
            ,{' '}
            <Link to="/error-groups" className="font-medium text-sky-800 underline">
              오류 그룹
            </Link>{' '}
            에서 지금 무엇이 터지고 있는지 확인하십시오.
          </p>
          <p className="mt-2 text-xs">
            화면이 메뉴를 감추는 것은 편의일 뿐입니다. 주소를 직접 입력해도 서버가 403 으로
            거절합니다.
          </p>
        </Notice>
      </Card>
    </div>
  );
}
