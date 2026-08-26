"""오류 그룹 목록·상세와 **fingerprint 기준 분석 이력 조인**.

Phase 1 담당 트랙: **정책 API**

핵심 검증은 하나다 — `error_groups` 는 조회 1 회 안에서만 유효하므로, 어제 분석한
오류가 오늘의 새 조회에서 "미분석"으로 보이면 그대로 중복 과금이 된다. 분석 이력을
`analysis_jobs` 에 직접 넣고 **새 조회의 새 그룹 id** 에 상태가 붙는지 본다.
"""

from __future__ import annotations

from datetime import timedelta

from app.enums import AnalysisJobStatus, Severity
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
    session_factory,
)


def test_list_error_groups_orders_by_count(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    make_error_group(db, run, fingerprint="fp-small", count=2)
    make_error_group(db, run, fingerprint="fp-big", count=90)

    response = client.get(f"/api/query-runs/{run.id}/error-groups")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query_run_id"] == run.id
    assert body["total"] == 2
    assert [item["fingerprint"] for item in body["items"]] == ["fp-big", "fp-small"]
    # 목록에는 대표 메시지·횟수·최초/최종 발생 시각만 — 원문 라인은 없다.
    assert "samples" not in body["items"][0]


def test_list_error_groups_paginates(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    for index in range(5):
        make_error_group(db, run, fingerprint=f"fp-{index}", count=10 - index)

    body = client.get(f"/api/query-runs/{run.id}/error-groups?limit=2&offset=2").json()
    assert body["total"] == 5
    assert [item["fingerprint"] for item in body["items"]] == ["fp-2", "fp-3"]


def test_list_error_groups_unknown_run_is_404(client) -> None:
    assert client.get("/api/query-runs/999/error-groups").status_code == 404


def test_analysis_status_joins_across_query_runs(client, db) -> None:
    """어제 조회의 그룹을 분석했으면, 오늘 조회의 **다른 id** 그룹도 분석됨으로 보인다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)

    yesterday = make_query_run(db, policy, started_at=NOW - timedelta(days=1))
    old_group = make_error_group(db, yesterday, fingerprint="fp-timeout", count=3)
    job = make_analysis_job(
        db, old_group, requested_at=NOW - timedelta(days=1), severity=Severity.HIGH.value
    )

    today = make_query_run(db, policy)
    new_group = make_error_group(db, today, fingerprint="fp-timeout", count=11)
    make_error_group(db, today, fingerprint="fp-fresh", count=1)

    items = client.get(f"/api/query-runs/{today.id}/error-groups").json()["items"]
    by_fingerprint = {item["fingerprint"]: item for item in items}

    assert new_group.id != old_group.id
    analyzed = by_fingerprint["fp-timeout"]
    assert analyzed["id"] == new_group.id
    assert analyzed["analysis_status"] == AnalysisJobStatus.SUCCEEDED.value
    assert analyzed["latest_analysis_job_id"] == job.id
    assert analyzed["latest_severity"] == Severity.HIGH.value

    never_analyzed = by_fingerprint["fp-fresh"]
    assert never_analyzed["analysis_status"] is None
    assert never_analyzed["latest_analysis_job_id"] is None
    assert never_analyzed["latest_severity"] is None


def test_analysis_status_uses_the_most_recent_job(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-timeout")

    make_analysis_job(
        db,
        group,
        status=AnalysisJobStatus.FAILED.value,
        requested_at=NOW - timedelta(hours=2),
        severity=None,
    )
    newest = make_analysis_job(
        db,
        group,
        status=AnalysisJobStatus.RUNNING.value,
        requested_at=NOW - timedelta(minutes=5),
        severity=None,
    )

    item = client.get(f"/api/query-runs/{run.id}/error-groups").json()["items"][0]
    assert item["analysis_status"] == AnalysisJobStatus.RUNNING.value
    assert item["latest_analysis_job_id"] == newest.id
    # 아직 결과가 없는 작업이므로 심각도는 비어 있다.
    assert item["latest_severity"] is None


def test_error_group_detail_includes_masked_samples_and_history(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)

    yesterday = make_query_run(db, policy, started_at=NOW - timedelta(days=1))
    old_group = make_error_group(db, yesterday, fingerprint="fp-timeout")
    old_job = make_analysis_job(db, old_group, requested_at=NOW - timedelta(days=1))

    today = make_query_run(db, policy)
    group = make_error_group(db, today, fingerprint="fp-timeout", count=11, samples=2)
    recent_job = make_analysis_job(db, group, requested_at=NOW - timedelta(minutes=1))

    response = client.get(f"/api/error-groups/{group.id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == group.id
    assert body["count"] == 11
    assert body["top_stack_frame"] == "payments/gateway.py:88"
    assert body["labels"]["environment"] == "staging"
    assert body["normalization_rule_version"] == "v1"

    assert len(body["samples"]) == 2
    for sample in body["samples"]:
        assert sample["masking_rule_version"] == "v1"
        assert "<REDACTED>" in sample["masked_log"]

    # 같은 fingerprint 의 과거 분석 이력이 조회 회차를 넘어 최신순으로 붙는다.
    assert [item["id"] for item in body["analyses"]] == [recent_job.id, old_job.id]
    assert body["analyses"][0]["severity"] == Severity.HIGH.value
    assert body["analyses"][0]["summary"].startswith("결제 게이트웨이")
    assert body["analysis_status"] == AnalysisJobStatus.SUCCEEDED.value


def test_error_group_detail_without_analysis(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-new", samples=1)

    body = client.get(f"/api/error-groups/{group.id}").json()
    assert body["analyses"] == []
    assert body["analysis_status"] is None
    assert body["trend"] == []


def test_error_group_detail_unknown_id_is_404(client) -> None:
    assert client.get("/api/error-groups/999").status_code == 404
