"""스케줄러 tick 과 정책 스케줄 필드 (Phase 5).

tick 은 백그라운드 루프를 띄우지 않고 **직접 호출**해서 검증한다 — 60 초를 기다리는
테스트는 쓸모가 없고, 루프는 `service.tick` 을 부르는 얇은 껍질이다.

계약상 제약이 걸린 지점 네 곳을 여기서 못 박는다.
- due 판정은 **마지막 query_run 의 started_at** 기준이다 (수동 실행도 포함).
- 자동 분석 대상은 **fingerprint 분석 이력이 전혀 없는** 그룹뿐이다.
- 일일 한도 초과·AI 분석 불허·LLM 연결 부재는 **예외 없이 스킵**한다.
- 스케줄이 만든 행은 `triggered_by="schedule"` 로 남는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import select

from app.enums import AnalysisJobStatus, QueryRunStatus, TriggeredBy
from app.models import AnalysisJob, AnalysisPolicy, ErrorGroup, QueryRun
from app.scheduler import service as scheduler
from tests.test_analysis_fixtures import (  # noqa: F401 - fixture 재수출
    FakeLLM,
    make_llm_connection,
    patched_llm,
    set_daily_limit,
)
from tests.test_policies_fixtures import (  # noqa: F401 - fixture 재수출
    NOW,
    FakeLogSource,
    FetchResult,
    client,
    db,
    engine,
    grouped_error,
    grouped_sample,
    log_record,
    make_analysis_job,
    make_connection,
    make_error_group,
    make_policy,
    make_query_run,
    no_real_log_source,
    session_factory,
)


def now() -> datetime:
    return datetime.now(UTC)


def make_scheduled_policy(db, connection, **overrides):
    values = {
        "schedule_enabled": True,
        "schedule_interval_minutes": 10,
        "auto_analyze_new": False,
    }
    values.update(overrides)
    return make_policy(db, connection, **values)


def fake_source(*fingerprints: str, records: int = 1) -> FakeLogSource:
    """조회 결과 1 건 + `group_records` 를 지정한 fingerprint 로 대체하기 위한 소스."""
    return FakeLogSource(
        fetch_result=FetchResult(records=[log_record("boom") for _ in range(records)])
    )


def run_tick(session_factory, *fingerprints: str, moment: datetime | None = None):
    """`build_provider` / `group_records` 를 가짜로 바꾸고 tick 을 1 회 돌린다."""
    groups = [grouped_error(fp, count=index + 1) for index, fp in enumerate(fingerprints)]
    with (
        patch(
            "app.policies.integrations.build_provider",
            side_effect=lambda connection: fake_source(),
        ),
        patch("app.policies.integrations.group_records", return_value=groups),
    ):
        return scheduler.tick(session_factory, now=moment or now())


# --------------------------------------------------------------- due 판정


def test_policy_without_a_run_is_due_immediately(db) -> None:
    connection = make_connection(db)
    policy = make_scheduled_policy(db, connection)
    assert scheduler.is_due(db, policy, now()) is True


def test_policy_is_not_due_before_the_interval_elapses(db) -> None:
    connection = make_connection(db)
    policy = make_scheduled_policy(db, connection, schedule_interval_minutes=30)
    make_query_run(db, policy, started_at=now() - timedelta(minutes=5))
    assert scheduler.is_due(db, policy, now()) is False


def test_policy_is_due_once_the_interval_has_elapsed(db) -> None:
    connection = make_connection(db)
    policy = make_scheduled_policy(db, connection, schedule_interval_minutes=30)
    make_query_run(db, policy, started_at=now() - timedelta(minutes=31))
    assert scheduler.is_due(db, policy, now()) is True


def test_due_is_measured_from_the_last_run_including_manual_ones(db) -> None:
    """방금 손으로 돌린 정책은 한 주기 미뤄진다 (같은 구간을 두 번 조회하지 않는다)."""
    connection = make_connection(db)
    policy = make_scheduled_policy(db, connection, schedule_interval_minutes=30)
    make_query_run(db, policy, started_at=now() - timedelta(minutes=40))
    assert scheduler.is_due(db, policy, now()) is True

    manual = make_query_run(db, policy, started_at=now() - timedelta(minutes=1))
    assert manual.triggered_by == TriggeredBy.MANUAL.value
    assert scheduler.is_due(db, policy, now()) is False


def test_a_failed_run_still_counts_as_a_run(db) -> None:
    """finished_at 이 아니라 started_at 이 기준이다 (실패 회차로 영원히 due 가 되지 않게)."""
    connection = make_connection(db)
    policy = make_scheduled_policy(db, connection, schedule_interval_minutes=30)
    make_query_run(
        db, policy, status=QueryRunStatus.FAILED.value, started_at=now() - timedelta(minutes=2)
    )
    assert scheduler.is_due(db, policy, now()) is False


def test_schedule_off_inactive_or_no_interval_is_never_due(db) -> None:
    connection = make_connection(db)
    off = make_policy(db, connection, name="off", schedule_enabled=False)
    inactive = make_scheduled_policy(db, connection, name="inactive", active=False)
    no_interval = make_policy(
        db, connection, name="no-interval", schedule_enabled=True, schedule_interval_minutes=None
    )
    moment = now()
    assert scheduler.is_due(db, off, moment) is False
    assert scheduler.is_due(db, inactive, moment) is False
    assert scheduler.is_due(db, no_interval, moment) is False
    assert scheduler.due_policies(db, moment) == []


# ----------------------------------------------------------- tick 실행 계약


def test_tick_runs_a_due_policy_and_marks_it_as_scheduled(db, session_factory) -> None:
    connection = make_connection(db)
    policy = make_scheduled_policy(db, connection)

    report = run_tick(session_factory, "fp-1")

    assert [result.policy_id for result in report.results] == [policy.id]
    run = db.scalars(select(QueryRun).where(QueryRun.policy_id == policy.id)).one()
    assert run.triggered_by == TriggeredBy.SCHEDULE.value
    assert run.status == QueryRunStatus.SUCCEEDED.value
    assert db.scalars(select(ErrorGroup).where(ErrorGroup.query_run_id == run.id)).all()


def test_tick_skips_policies_that_are_not_due(db, session_factory) -> None:
    connection = make_connection(db)
    policy = make_scheduled_policy(db, connection, schedule_interval_minutes=60)
    make_query_run(db, policy, started_at=now() - timedelta(minutes=1))

    report = run_tick(session_factory, "fp-1")

    assert report.results == []
    assert len(db.scalars(select(QueryRun)).all()) == 1


def test_a_failing_policy_does_not_stop_the_other_policies(db, session_factory) -> None:
    connection = make_connection(db)
    first = make_scheduled_policy(db, connection, name="first")
    second = make_scheduled_policy(db, connection, name="second")

    with patch(
        "app.scheduler.service.create_query_run",
        side_effect=[RuntimeError("Loki down"), _fake_run_read(second.id)],
    ):
        report = scheduler.tick(session_factory)

    assert [result.policy_id for result in report.results] == [first.id, second.id]
    assert report.results[0].error is not None
    assert report.results[1].error is None


def _fake_run_read(policy_id: int):
    from app.schemas.api import QueryRunRead

    moment = now()
    return QueryRunRead(
        id=999,
        policy_id=policy_id,
        status=QueryRunStatus.SUCCEEDED,
        started_at=moment,
        range_start=moment - timedelta(minutes=60),
        range_end=moment,
        triggered_by=TriggeredBy.SCHEDULE,
    )


def test_overlapping_ticks_are_skipped(db, session_factory) -> None:
    """겹침 방지는 in-process 락이다 (단일 워커 전제)."""
    connection = make_connection(db)
    make_scheduled_policy(db, connection)

    scheduler._TICK_LOCK.acquire()
    try:
        report = scheduler.tick(session_factory)
    finally:
        scheduler._TICK_LOCK.release()

    assert report.skipped_overlap is True
    assert report.results == []
    assert db.scalars(select(QueryRun)).all() == []


# ------------------------------------------------------------- 자동 분석


def test_auto_analyze_only_touches_brand_new_fingerprints(db, session_factory) -> None:
    connection = make_connection(db)
    policy = make_scheduled_policy(db, connection, auto_analyze_new=True)
    make_llm_connection(db)

    # "fp-old" 는 과거 조회에서 이미 분석된 적이 있다.
    old_run = make_query_run(db, policy, started_at=now() - timedelta(hours=2))
    old_group = make_error_group(db, old_run, fingerprint="fp-old")
    make_analysis_job(db, old_group, fingerprint="fp-old")

    with patched_llm() as llm:
        run_tick(session_factory, "fp-old", "fp-new")

    analyzed = {
        job.fingerprint
        for job in db.scalars(
            select(AnalysisJob).where(AnalysisJob.triggered_by == TriggeredBy.SCHEDULE.value)
        ).all()
    }
    assert analyzed == {"fp-new"}, "이미 분석 이력이 있는 fingerprint 는 다시 태우지 않는다"
    assert len(llm.prompts) == 1


def test_auto_analyze_records_triggered_by_schedule_and_succeeds(db, session_factory) -> None:
    connection = make_connection(db)
    make_scheduled_policy(db, connection, auto_analyze_new=True)
    make_llm_connection(db)

    with patched_llm():
        run_tick(session_factory, "fp-1")

    db.expire_all()
    job = db.scalars(select(AnalysisJob)).one()
    assert job.triggered_by == TriggeredBy.SCHEDULE.value
    assert job.status == AnalysisJobStatus.SUCCEEDED.value


def test_a_failed_analysis_is_not_retried_on_the_next_tick(db, session_factory) -> None:
    """실패도 "이력 있음" 이다 — 미분석으로 세면 같은 실패를 매 회차 다시 태운다."""
    connection = make_connection(db)
    policy = make_scheduled_policy(db, connection, auto_analyze_new=True, schedule_interval_minutes=1)
    make_llm_connection(db)

    with patched_llm(FakeLLM(error=RuntimeError("LLM down"))):
        run_tick(session_factory, "fp-1")

    db.expire_all()
    first = db.scalars(select(AnalysisJob)).one()
    assert first.status == AnalysisJobStatus.FAILED.value

    # 다음 회차 — 같은 fingerprint 는 이제 "이력 있음" 이라 건너뛴다.
    db.execute(
        QueryRun.__table__.update().values(started_at=now() - timedelta(minutes=10))
    )
    db.commit()
    with patched_llm() as llm:
        run_tick(session_factory, "fp-1")

    assert llm.prompts == []
    db.expire_all()
    assert len(db.scalars(select(AnalysisJob)).all()) == 1


def test_auto_analyze_stops_at_the_daily_limit_without_raising(db, session_factory) -> None:
    connection = make_connection(db)
    make_scheduled_policy(db, connection, auto_analyze_new=True)
    make_llm_connection(db)
    set_daily_limit(db, 1)

    with patched_llm() as llm:
        report = run_tick(session_factory, "fp-1", "fp-2", "fp-3")

    db.expire_all()
    jobs = db.scalars(select(AnalysisJob)).all()
    assert len(jobs) == 1, "한도를 넘으면 더 만들지 않는다"
    assert len(llm.prompts) == 1
    assert "daily_limit" in report.results[0].skipped
    assert report.results[0].error is None, "한도 초과는 예외가 아니라 스킵이다"
    # 조회 자체는 성공했다 — 분석 한도가 조회를 막지는 않는다.
    assert report.results[0].run_status == QueryRunStatus.SUCCEEDED.value


def test_auto_analyze_is_skipped_when_the_policy_forbids_ai(db, session_factory) -> None:
    connection = make_connection(db)
    make_scheduled_policy(db, connection, auto_analyze_new=True, allow_ai_analysis=False)
    make_llm_connection(db)

    with patched_llm() as llm:
        report = run_tick(session_factory, "fp-1")

    assert db.scalars(select(AnalysisJob)).all() == []
    assert llm.prompts == []
    assert "analysis_not_allowed" in report.results[0].skipped


def test_auto_analyze_is_skipped_when_no_llm_connection_exists(db, session_factory) -> None:
    connection = make_connection(db)
    make_scheduled_policy(db, connection, auto_analyze_new=True)

    with patched_llm() as llm:
        report = run_tick(session_factory, "fp-1")

    assert db.scalars(select(AnalysisJob)).all() == []
    assert llm.prompts == []
    assert "llm_unavailable" in report.results[0].skipped


def test_auto_analyze_off_means_no_analysis_at_all(db, session_factory) -> None:
    connection = make_connection(db)
    make_scheduled_policy(db, connection, auto_analyze_new=False)
    make_llm_connection(db)

    with patched_llm() as llm:
        run_tick(session_factory, "fp-1")

    assert db.scalars(select(AnalysisJob)).all() == []
    assert llm.prompts == []


def test_a_failed_query_run_does_not_trigger_analysis(db, session_factory) -> None:
    connection = make_connection(db)
    make_scheduled_policy(db, connection, auto_analyze_new=True)
    make_llm_connection(db)

    with (
        patch(
            "app.policies.integrations.build_provider",
            side_effect=lambda conn: FakeLogSource(fetch_error=RuntimeError("Loki down")),
        ),
        patched_llm() as llm,
    ):
        report = scheduler.tick(session_factory)

    assert report.results[0].run_status == QueryRunStatus.FAILED.value
    assert llm.prompts == []
    assert db.scalars(select(AnalysisJob)).all() == []


# --------------------------------------------------- 스케줄 필드 CRUD 계약


def _policy_body(connection_id: int, **overrides) -> dict:
    body = {
        "loki_connection_id": connection_id,
        "name": "scheduled",
        "logql": '{service="payment-api"} | json | level="ERROR"',
    }
    body.update(overrides)
    return body


def test_policy_defaults_have_scheduling_off(client, db) -> None:
    connection = make_connection(db)
    created = client.post("/api/policies", json=_policy_body(connection.id)).json()
    assert created["schedule_enabled"] is False
    assert created["schedule_interval_minutes"] is None
    assert created["auto_analyze_new"] is False


def test_policy_round_trips_the_schedule_fields(client, db) -> None:
    connection = make_connection(db)
    body = _policy_body(
        connection.id,
        schedule_enabled=True,
        schedule_interval_minutes=15,
        auto_analyze_new=True,
    )
    created = client.post("/api/policies", json=body).json()
    assert created["schedule_enabled"] is True
    assert created["schedule_interval_minutes"] == 15
    assert created["auto_analyze_new"] is True

    fetched = client.get(f"/api/policies/{created['id']}").json()
    assert fetched["schedule_interval_minutes"] == 15

    patched = client.patch(
        f"/api/policies/{created['id']}", json={"schedule_interval_minutes": 45}
    ).json()
    assert patched["schedule_interval_minutes"] == 45


def test_enabling_a_schedule_without_an_interval_is_422(client, db) -> None:
    connection = make_connection(db)
    response = client.post(
        "/api/policies", json=_policy_body(connection.id, schedule_enabled=True)
    )
    assert response.status_code == 422
    assert "schedule_interval_minutes" in response.json()["detail"]


def test_clearing_the_interval_of_an_enabled_schedule_is_422(client, db) -> None:
    """병합 후 실효값으로 검증하지 않으면 "주기만 지운" 요청이 무주기 정책을 만든다."""
    connection = make_connection(db)
    created = client.post(
        "/api/policies",
        json=_policy_body(connection.id, schedule_enabled=True, schedule_interval_minutes=10),
    ).json()

    response = client.patch(
        f"/api/policies/{created['id']}", json={"schedule_interval_minutes": None}
    )
    assert response.status_code == 422

    # 스케줄을 함께 끄면 통과한다.
    ok = client.patch(
        f"/api/policies/{created['id']}",
        json={"schedule_enabled": False, "schedule_interval_minutes": None},
    )
    assert ok.status_code == 200
    assert ok.json()["schedule_interval_minutes"] is None


def test_query_run_response_exposes_triggered_by(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    body = client.get(f"/api/query-runs/{run.id}").json()
    assert body["triggered_by"] == "manual"


def test_analysis_job_response_exposes_triggered_by(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-1")
    job = make_analysis_job(db, group)
    body = client.get(f"/api/analysis-jobs/{job.id}").json()
    assert body["triggered_by"] == "manual"


def test_scheduled_policies_are_listed_by_the_planner(db) -> None:
    connection = make_connection(db)
    a = make_scheduled_policy(db, connection, name="a")
    make_policy(db, connection, name="b")
    c = make_scheduled_policy(db, connection, name="c")
    assert [policy.id for policy in scheduler.due_policies(db, now())] == [a.id, c.id]


def test_policy_orm_defaults_are_off(db) -> None:
    connection = make_connection(db)
    policy = AnalysisPolicy(
        loki_connection_id=connection.id,
        name="plain",
        logql="{}",
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    assert policy.schedule_enabled is False
    assert policy.auto_analyze_new is False
    assert policy.schedule_interval_minutes is None
