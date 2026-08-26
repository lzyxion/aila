"""`GET /api/dashboard/error-groups` — 정책을 넘나드는 오류 그룹 목록 (Phase 6).

기존 `/api/query-runs/{id}/error-groups` 는 **조회 1 회**의 그룹을 준다. 첫 화면에서
"지금 무엇이 제일 많이 터지고 있는가"를 보려면 정책마다 그 목록을 열어 봐야 했다.
이 엔드포인트는 그 질문에 한 번에 답한다.

>>> 무엇을 모으는가 <<<
**활성 정책별 최신 성공 query-run** 의 그룹들이다. 셋 다 이유가 있다.

- *활성 정책* — 비활성 정책의 마지막 회차까지 섞으면 이미 끄기로 한 오류가 상위를
  차지한다 (`summary` 가 비활성 정책을 빼지 않는 것과 반대인데, 거기는 "정책의
  상태"가 주제이고 여기는 "지금 터지는 오류"가 주제라서 그렇다).
- *최신* — 그룹은 회차마다 새로 생긴다. 회차를 안 좁히면 같은 오류가 회차 수만큼
  중복으로 나온다.
- *성공* — 실패한 회차는 그룹이 아예 없거나 부분만 있다. 그것을 최신으로 잡으면
  화면이 조용히 비어 버린다.

정렬은 `count desc, last_seen desc` 다. 분석 상태·severity 는 기존 그룹 목록과
**같은 경로**(fingerprint 조인)로 붙인다 — 판정 규칙이 화면마다 갈라지지 않게.

N+1 을 만들지 않는다: 최신 회차 판정은 윈도 함수 서브쿼리 하나, 목록은 그 서브쿼리와
조인한 한 번의 쿼리, 분석 상태는 페이지 fingerprint 에 대한 한 번의 조회다.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import QueryRunStatus
from app.error_groups import service as error_group_service
from app.models import AnalysisPolicy, ErrorGroup, QueryRun
from app.schemas.api import DashboardErrorGroupItem, DashboardErrorGroupListResponse


def _latest_successful_runs():
    """정책별 **최신 성공 회차** 서브쿼리 (활성 정책만).

    `max(started_at)` 이 아니라 윈도 함수를 쓰는 이유는 동률 때문이다 — 같은 초에
    들어간 두 회차가 있으면 max 로는 둘 다 남아 그룹이 중복된다. 정렬 키를
    `started_at desc, id desc` 로 두면 실행 이력 목록과 "최신"의 정의가 같아진다.
    """
    ranked = (
        select(
            QueryRun.id.label("run_id"),
            QueryRun.policy_id.label("policy_id"),
            func.row_number()
            .over(
                partition_by=QueryRun.policy_id,
                order_by=(QueryRun.started_at.desc(), QueryRun.id.desc()),
            )
            .label("rank"),
        )
        .join(AnalysisPolicy, AnalysisPolicy.id == QueryRun.policy_id)
        .where(
            QueryRun.status == QueryRunStatus.SUCCEEDED.value,
            AnalysisPolicy.active.is_(True),
        )
        .subquery()
    )
    return select(ranked.c.run_id, ranked.c.policy_id).where(ranked.c.rank == 1).subquery()


def list_error_groups(
    db: Session, *, limit: int = 50, offset: int = 0
) -> DashboardErrorGroupListResponse:
    """전 활성 정책의 최신 성공 회차 그룹을 count 내림차순으로 모은다."""
    latest = _latest_successful_runs()

    base = (
        select(ErrorGroup, AnalysisPolicy.id, AnalysisPolicy.name)
        .join(latest, latest.c.run_id == ErrorGroup.query_run_id)
        .join(AnalysisPolicy, AnalysisPolicy.id == latest.c.policy_id)
    )

    total = int(
        db.scalar(
            select(func.count())
            .select_from(ErrorGroup)
            .join(latest, latest.c.run_id == ErrorGroup.query_run_id)
        )
        or 0
    )

    rows = db.execute(
        base.order_by(
            ErrorGroup.count.desc(),
            ErrorGroup.last_seen.desc(),
            # 동률에서 순서가 흔들리면 페이지 경계에서 행이 중복되거나 사라진다.
            ErrorGroup.id.asc(),
        )
        .offset(offset)
        .limit(limit)
    ).all()

    groups = [group for group, _policy_id, _policy_name in rows]
    # 분석 상태·severity 는 fingerprint 기준 — 페이지 전체를 한 번에 조회한다.
    latest_analysis = error_group_service.latest_analysis_by_fingerprint(
        db, [group.fingerprint for group in groups]
    )

    items = [
        DashboardErrorGroupItem(
            **error_group_service.to_summary(group, latest_analysis).model_dump(),
            policy_id=policy_id,
            policy_name=policy_name,
        )
        for group, policy_id, policy_name in rows
    ]

    return DashboardErrorGroupListResponse(
        total=total, limit=limit, offset=offset, items=items
    )


__all__ = ["list_error_groups"]
