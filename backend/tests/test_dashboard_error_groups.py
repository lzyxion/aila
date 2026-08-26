"""전체 오류 그룹 리스트 (Phase 6) — `GET /api/dashboard/error-groups`.

정책 하나의 그룹 목록은 기존 `/query-runs/{id}/error-groups` 가 담당한다. 이쪽은
**전 활성 정책의 최신 성공 회차**를 한데 모아 "지금 무엇이 제일 많이 터지는가"에
답한다. 그래서 이 모듈이 고정하는 것은 대부분 "무엇을 **빼는가**" 이다.

- 오래된 회차를 빼지 않으면 같은 오류가 회차 수만큼 중복으로 나온다.
- 실패한 회차를 최신으로 잡으면 목록이 조용히 빈다 (그룹이 없거나 부분만 있다).
- 비활성 정책을 섞으면 이미 끄기로 한 오류가 상위를 차지한다.
- 분석 상태·severity 는 기존 그룹 목록과 **같은 fingerprint 조인**으로 붙는다.
"""

from __future__ import annotations

from datetime import timedelta

from app.enums import AnalysisJobStatus, QueryRunStatus, Severity
from tests.test_policies_fixtures import (  # noqa: F401 - fixture 재수출
    NOW,
    client,
    db,
    engine,
    make_analysis_job,
    make_connection,
    make_error_group,
    make_policy,
    make_query_run,
    no_real_log_source,
    session_factory,
)


def feed(client, **params):
    response = client.get("/api/dashboard/error-groups", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_collects_groups_from_every_active_policy(client, db) -> None:
    connection = make_connection(db)
    payment = make_policy(db, connection, name="payment")
    auth = make_policy(db, connection, name="auth")
    make_error_group(db, make_query_run(db, payment), fingerprint="fp-p", count=5)
    make_error_group(db, make_query_run(db, auth), fingerprint="fp-a", count=9)

    body = feed(client)

    assert body["total"] == 2
    assert body["limit"] == 50 and body["offset"] == 0
    # count 내림차순 — "지금 제일 많이 터지는 것" 이 첫 줄이어야 한다.
    assert [item["fingerprint"] for item in body["items"]] == ["fp-a", "fp-p"]
    assert body["items"][0]["policy_id"] == auth.id
    assert body["items"][0]["policy_name"] == "auth"


def test_only_the_latest_successful_run_of_each_policy_counts(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    older = make_query_run(db, policy, started_at=NOW - timedelta(hours=2))
    latest = make_query_run(db, policy, started_at=NOW)
    make_error_group(db, older, fingerprint="fp-same", count=99)
    make_error_group(db, latest, fingerprint="fp-same", count=3)

    body = feed(client)

    # 회차를 안 좁히면 같은 오류가 두 줄이 되고, 99 가 먼저 나와 옛 회차를 현재로 읽는다.
    assert body["total"] == 1
    assert body["items"][0]["query_run_id"] == latest.id
    assert body["items"][0]["count"] == 3


def test_a_failed_latest_run_falls_back_to_the_latest_successful_one(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    succeeded = make_query_run(db, policy, started_at=NOW - timedelta(hours=1))
    make_error_group(db, succeeded, fingerprint="fp-ok", count=4)
    make_query_run(db, policy, status=QueryRunStatus.FAILED.value, started_at=NOW)

    body = feed(client)

    # 실패 회차를 "최신"으로 잡으면 목록이 조용히 빈다.
    assert [item["fingerprint"] for item in body["items"]] == ["fp-ok"]


def test_ties_on_started_at_pick_one_run_not_both(client, db) -> None:
    """같은 초에 들어간 두 회차를 max(started_at) 으로 고르면 그룹이 중복된다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    first = make_query_run(db, policy, started_at=NOW)
    second = make_query_run(db, policy, started_at=NOW)
    make_error_group(db, first, fingerprint="fp-1", count=2)
    make_error_group(db, second, fingerprint="fp-1", count=2)

    body = feed(client)

    assert body["total"] == 1
    assert body["items"][0]["query_run_id"] == second.id


def test_inactive_policies_are_excluded(client, db) -> None:
    connection = make_connection(db)
    live = make_policy(db, connection, name="live")
    retired = make_policy(db, connection, name="retired", active=False)
    make_error_group(db, make_query_run(db, live), fingerprint="fp-live", count=1)
    make_error_group(db, make_query_run(db, retired), fingerprint="fp-dead", count=500)

    body = feed(client)

    assert [item["fingerprint"] for item in body["items"]] == ["fp-live"]


def test_policies_without_a_successful_run_contribute_nothing(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    failed = make_query_run(db, policy, status=QueryRunStatus.FAILED.value)
    make_error_group(db, failed, fingerprint="fp-partial", count=3)

    assert feed(client)["total"] == 0


def test_analysis_status_is_joined_by_fingerprint(client, db) -> None:
    """그룹 id 가 아니라 fingerprint 기준 — 회차가 바뀌어도 "이미 분석함" 이 유지된다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    old_run = make_query_run(db, policy, started_at=NOW - timedelta(hours=3))
    old_group = make_error_group(db, old_run, fingerprint="fp-known", count=2)
    job = make_analysis_job(db, old_group, severity=Severity.HIGH.value)

    new_run = make_query_run(db, policy, started_at=NOW)
    make_error_group(db, new_run, fingerprint="fp-known", count=2)
    make_error_group(db, new_run, fingerprint="fp-new", count=1)

    rows = {item["fingerprint"]: item for item in feed(client)["items"]}

    assert rows["fp-known"]["analysis_status"] == AnalysisJobStatus.SUCCEEDED.value
    assert rows["fp-known"]["latest_analysis_job_id"] == job.id
    assert rows["fp-known"]["latest_severity"] == Severity.HIGH.value
    assert rows["fp-new"]["analysis_status"] is None


def test_pagination_keeps_total_and_order_stable(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    for index in range(5):
        make_error_group(db, run, fingerprint=f"fp-{index}", count=10 - index)

    first = feed(client, limit=2, offset=0)
    second = feed(client, limit=2, offset=2)

    assert first["total"] == second["total"] == 5
    assert first["limit"] == 2 and second["offset"] == 2
    assert [item["fingerprint"] for item in first["items"]] == ["fp-0", "fp-1"]
    assert [item["fingerprint"] for item in second["items"]] == ["fp-2", "fp-3"]


def test_empty_when_there_are_no_policies(client, db) -> None:
    body = feed(client)
    assert body == {"total": 0, "limit": 50, "offset": 0, "items": []}
