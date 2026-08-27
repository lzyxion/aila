/**
 * 404.
 *
 * 이 화면은 Phase 8 컨벤션의 **최소 예시**다: 머리말은 한 줄, 되돌아갈 길은
 * `ButtonLink`(버튼 모양 링크), 빈 상태는 아이콘 + 글자, 색은 전부 의미 토큰.
 */

import { DashboardIcon, EmptyIcon, ErrorGroupIcon, PolicyIcon } from '../components/icons';
import { ButtonLink, Card, EmptyBlock, PageHeader } from '../components/ui';

export function NotFoundPage() {
  return (
    <div>
      <PageHeader
        title="페이지를 찾을 수 없습니다"
        description="주소가 바뀌었거나, 삭제된 정책·그룹의 링크일 수 있습니다."
      />
      <Card>
        <EmptyBlock icon={EmptyIcon}>
          요청한 경로에 해당하는 화면이 없습니다.
          <span className="mt-4 flex flex-wrap justify-center gap-2">
            <ButtonLink to="/" variant="primary">
              <DashboardIcon aria-hidden className="size-4" />
              대시보드
            </ButtonLink>
            <ButtonLink to="/policies">
              <PolicyIcon aria-hidden className="size-4" />
              분석 정책
            </ButtonLink>
            <ButtonLink to="/error-groups">
              <ErrorGroupIcon aria-hidden className="size-4" />
              오류 그룹
            </ButtonLink>
          </span>
        </EmptyBlock>
      </Card>
    </div>
  );
}
