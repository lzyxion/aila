"""스케줄 tick — due 판정 → 정책 실행 → (선택) 신규 fingerprint 자동 분석.

>>> 단일 uvicorn 워커 전제 <<<
겹침 방지는 **in-process 락**(`_TICK_LOCK`)이다. 워커를 2 개 이상 띄우면 각
프로세스가 자기 락만 보므로 같은 정책이 동시에 두 번 돌 수 있다. DB 어드바이저리
락으로 올리는 것은 어렵지 않지만, 이 스택은 로컬·데모용 단일 워커 배포이고 (compose
의 backend 서비스도 워커 1 개다) 지금 필요 없는 분산 조정을 미리 넣지 않는다.
**워커를 늘리려면 여기부터 고쳐야 한다** — 늘려도 조용히 동작하는 것처럼 보이므로
이 문장이 유일한 경고다.

>>> due 판정 기준 <<<
"마지막 `query_run` 의 **started_at** 이후 interval 이 지났는가" 다. finished_at 이
아닌 이유는, 실패해서 finished_at 이 비어 있는 회차가 하나 있으면 그 정책이 영원히
due 로 남거나(NULL 취급에 따라) 영원히 안 돌기 때문이다. 시작 시각은 성공·실패와
무관하게 항상 있다. 수동 실행도 같은 테이블에 남으므로, 방금 손으로 돌린 정책은
자연히 한 주기 미뤄진다 — 이것이 의도한 동작이다(중복 조회 방지).

>>> 자동 분석의 범위 <<<
`auto_analyze_new=true` 인 정책만, 그 회차 그룹 중 **fingerprint 분석 이력이 전혀
없는 것**만 분석한다. 진입점은 `analysis.service.create_analysis_job` 하나뿐이라
`allow_ai_analysis` · 멱등 · 일일 한도(429)가 그대로 상한이다. 한도 초과·연결 부재는
예외를 올리지 않고 스킵하고 로그만 남긴다 — 스케줄러가 죽으면 조회까지 멈춘다.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.analysis.service import create_analysis_job, run_analysis_job
from app.enums import AnalysisJobStatus, QueryRunStatus, TriggeredBy
from app.models import AnalysisJob, AnalysisPolicy, ErrorGroup, QueryRun
from app.policies.service import create_query_run
from app.schemas.api import AnalysisJobCreateRequest, QueryRunCreateRequest

logger = logging.getLogger(__name__)

#: tick 겹침 방지용 in-process 락. 단일 워커 전제 (모듈 docstring 참고).
_TICK_LOCK = threading.Lock()


@dataclass
class PolicyTickResult:
    """정책 하나에 대한 tick 결과 (로그·테스트용)."""

    policy_id: int
    query_run_id: int | None = None
    run_status: str | None = None
    analyzed_group_ids: list[int] = field(default_factory=list)
    #: 자동 분석을 중단·건너뛴 사유 코드 (`daily_limit`, `analysis_not_allowed`, ...).
    skipped: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class TickReport:
    started_at: datetime
    #: 다른 tick 이 아직 돌고 있어 이번 회차를 통째로 건너뛰었다.
    skipped_overlap: bool = False
    results: list[PolicyTickResult] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 는 tz 를 버리고 돌려준다 — naive 는 UTC 로 간주한다."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def last_run_started_at(db: Session, policy_id: int) -> datetime | None:
    """정책의 가장 최근 `query_run.started_at` (성공·실패·수동 모두 포함)."""
    run = db.scalars(
        select(QueryRun)
        .where(QueryRun.policy_id == policy_id)
        .order_by(QueryRun.started_at.desc(), QueryRun.id.desc())
        .limit(1)
    ).first()
    return _as_utc(run.started_at) if run is not None else None


def is_due(db: Session, policy: AnalysisPolicy, now: datetime) -> bool:
    """실행할 때가 됐는가. 이력이 아예 없으면 즉시 due 다."""
    interval = policy.schedule_interval_minutes
    if not policy.active or not policy.schedule_enabled or not interval or interval <= 0:
        return False
    last = last_run_started_at(db, policy.id)
    if last is None:
        return True
    return now - last >= timedelta(minutes=interval)


def due_policies(db: Session, now: datetime) -> list[AnalysisPolicy]:
    """이번 tick 에 돌려야 할 정책 (id 오름차순 — 순서를 결정적으로 둔다)."""
    candidates = db.scalars(
        select(AnalysisPolicy)
        .where(
            AnalysisPolicy.active.is_(True),
            AnalysisPolicy.schedule_enabled.is_(True),
            AnalysisPolicy.schedule_interval_minutes.is_not(None),
        )
        .order_by(AnalysisPolicy.id.asc())
    ).all()
    return [policy for policy in candidates if is_due(db, policy, now)]


def _new_fingerprint_group_ids(db: Session, run_id: int) -> list[int]:
    """이 회차 그룹 중 **fingerprint 분석 이력이 전혀 없는** 그룹 id (발생 많은 순).

    상태를 보지 않는 것이 핵심이다 — 실패한 분석도 "이력 있음" 으로 친다.
    실패를 미분석으로 세면 같은 실패를 매 회차 자동으로 다시 태운다(=반복 과금).
    """
    exists = (
        select(AnalysisJob.id).where(AnalysisJob.fingerprint == ErrorGroup.fingerprint).exists()
    )
    rows = db.execute(
        select(ErrorGroup.id)
        .where(ErrorGroup.query_run_id == run_id, ~exists)
        .order_by(ErrorGroup.count.desc(), ErrorGroup.id.asc())
    ).all()
    return [row[0] for row in rows]


def _auto_analyze(
    db: Session,
    factory: sessionmaker[Session],
    policy: AnalysisPolicy,
    run_id: int,
    result: PolicyTickResult,
) -> None:
    """신규 fingerprint 자동 분석. 기존 `create_analysis_job` 경로를 그대로 탄다."""
    group_ids = _new_fingerprint_group_ids(db, run_id)
    if not group_ids:
        return

    for group_id in group_ids:
        try:
            created = create_analysis_job(
                db,
                group_id,
                AnalysisJobCreateRequest(),
                None,  # BackgroundTasks 없음 — 아래에서 동기 실행한다
                triggered_by=TriggeredBy.SCHEDULE.value,
            )
        except HTTPException as exc:
            # 429(한도)·403(정책이 AI 분석 불허)·422(LLM 연결 없음)는 이번 회차의
            # 나머지 그룹에도 똑같이 걸린다 — 계속 시도해도 같은 응답이라 중단한다.
            code = {429: "daily_limit", 403: "analysis_not_allowed", 422: "llm_unavailable"}.get(
                exc.status_code, f"http_{exc.status_code}"
            )
            result.skipped.append(code)
            logger.info(
                "scheduler: 정책 %s 자동 분석 중단 (%s) — %s", policy.id, code, exc.detail
            )
            return
        except Exception as exc:  # noqa: BLE001 - 분석 실패로 tick 을 죽이지 않는다
            db.rollback()
            result.skipped.append("error")
            logger.warning(
                "scheduler: 정책 %s 그룹 %s 자동 분석 생성 실패: %r", policy.id, group_id, exc
            )
            return

        if created.reused:
            # 같은 fingerprint 가 이미 돌고 있다 (수동 요청과 겹친 경우). 재실행하지 않는다.
            continue
        if created.status == AnalysisJobStatus.PENDING:
            # 요청 세션이 아니라 새 세션에서 돈다 (BackgroundTasks 와 같은 규칙).
            run_analysis_job(factory, created.id)
        result.analyzed_group_ids.append(group_id)


def run_policy(
    db: Session, factory: sessionmaker[Session], policy: AnalysisPolicy
) -> PolicyTickResult:
    """정책 1 건 실행 (+ 필요하면 자동 분석). 실패는 결과에 담고 올리지 않는다."""
    result = PolicyTickResult(policy_id=policy.id)
    try:
        run = create_query_run(
            db,
            policy.id,
            QueryRunCreateRequest(),
            triggered_by=TriggeredBy.SCHEDULE.value,
        )
    except Exception as exc:  # noqa: BLE001 - 정책 하나의 실패로 tick 전체를 죽이지 않는다
        db.rollback()
        result.error = f"{type(exc).__name__}: {exc}"
        logger.warning("scheduler: 정책 %s 실행 실패: %r", policy.id, exc)
        return result

    result.query_run_id = run.id
    result.run_status = run.status.value if hasattr(run.status, "value") else str(run.status)
    logger.info(
        "scheduler: 정책 %s 실행 (run=%s status=%s groups=%s)",
        policy.id,
        run.id,
        result.run_status,
        run.group_count,
    )

    if result.run_status != QueryRunStatus.SUCCEEDED.value:
        return result
    if not policy.auto_analyze_new:
        return result

    _auto_analyze(db, factory, policy, run.id, result)
    return result


def tick(factory: sessionmaker[Session], *, now: datetime | None = None) -> TickReport:
    """한 회차. 겹치면 통째로 건너뛴다 (in-process 락 — 단일 워커 전제).

    조회는 순차 실행이다. 병렬로 돌리면 Loki 에 동시 부하가 걸리고, 일일 분석 한도
    검사가 여러 스레드에서 동시에 일어나 한도가 뚫린다.
    """
    moment = now or _now()
    report = TickReport(started_at=moment)

    if not _TICK_LOCK.acquire(blocking=False):
        report.skipped_overlap = True
        logger.info("scheduler: 이전 tick 이 아직 돌고 있어 이번 회차를 건너뜁니다.")
        return report

    try:
        db = factory()
        try:
            policies = due_policies(db, moment)
            for policy in policies:
                report.results.append(run_policy(db, factory, policy))
        finally:
            db.close()
    finally:
        _TICK_LOCK.release()
    return report


__all__ = [
    "PolicyTickResult",
    "TickReport",
    "due_policies",
    "is_due",
    "last_run_started_at",
    "run_policy",
    "tick",
]
