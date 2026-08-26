"""오류 그룹 목록·상세와 **fingerprint 기준 분석 이력 조인**.

Phase 1 담당 트랙: **정책 API**

핵심 검증은 하나다 — `error_groups` 는 조회 1 회 안에서만 유효하므로, 어제 분석한
오류가 오늘의 새 조회에서 "미분석"으로 보이면 그대로 중복 과금이 된다. 분석 이력을
`analysis_jobs` 에 직접 넣고 **새 조회의 새 그룹 id** 에 상태가 붙는지 본다.
"""

from __future__ import annotations

from datetime import UTC, timedelta
from unittest.mock import patch

from app.enums import AnalysisJobStatus, Severity
from app.error_groups import service
from app.providers.logsource import LogSourceError
from tests.test_policies_fixtures import (  # noqa: F401 - fixture 재수출
    NOW,
    FakeLogSource,
    client,
    count_series,
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


# --------------------------------------------------------------- 발생 추이


def _detail(client, provider, group_id: int):
    with patch("app.policies.integrations.build_provider", return_value=provider):
        return client.get(f"/api/error-groups/{group_id}").json()


def test_detail_trend_comes_from_a_metric_query(client, db) -> None:
    """추이는 저장된 라인이 아니라 `count_over_time` 으로 채운다 (계약).

    저장된 대표 로그는 그룹당 최대 3 건이고 `count` 는 조회 상한에 잘린 값이라,
    둘 중 어느 것도 추이가 될 수 없다.
    """
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-timeout", count=11)
    provider = FakeLogSource(count_series=count_series(("payment-api", 3.0), ("payment-api", 5.0)))

    body = _detail(client, provider, group.id)

    assert [point["value"] for point in body["trend"]] == [3.0, 5.0]
    assert body["trend_warnings"] == []

    # 그룹 라벨로 만든 selector 를 그대로 쓴다 (보고서의 재조회 조건과 같은 값).
    query, time_range, step = provider.count_calls[0]
    assert query == '{environment="staging", service="payment-api"}'
    assert step >= 15
    # 그룹 발생 구간을 여유와 함께 덮는다.
    assert time_range.start < group.first_seen.replace(tzinfo=UTC)
    assert time_range.end > group.last_seen.replace(tzinfo=UTC)


def test_detail_trend_failure_is_reported_not_swallowed(client, db) -> None:
    """빈 배열만 주면 '오류가 없었다'와 '조회하지 못했다'가 구분되지 않는다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-timeout")

    body = _detail(client, FakeLogSource(count_error=LogSourceError("Loki 503")), group.id)

    assert body["trend"] == []
    assert [w["code"] for w in body["trend_warnings"]] == ["trend_query_failed"]
    assert "Loki 503" in body["trend_warnings"][0]["message"]


def test_detail_trend_without_labels_is_reported(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-nolabels", labels={})

    body = _detail(client, FakeLogSource(), group.id)

    assert body["trend"] == []
    assert [w["code"] for w in body["trend_warnings"]] == ["trend_no_labels"]


def test_detail_trend_with_an_inactive_connection_is_reported(client, db) -> None:
    connection = make_connection(db, active=False)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-timeout")

    body = client.get(f"/api/error-groups/{group.id}").json()

    assert body["trend"] == []
    assert [w["code"] for w in body["trend_warnings"]] == ["trend_connection_unavailable"]


# --------------------------------------------- active(진행 중) 전용 조회


def test_active_lookup_finds_a_running_job_behind_a_newer_failure(client, db) -> None:
    """"최신 1 건" 으로 판정하면 뒤에 실패가 하나 끼는 순간 실행 중인 작업을 놓친다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-timeout")

    running = make_analysis_job(
        db,
        group,
        status=AnalysisJobStatus.RUNNING.value,
        requested_at=NOW - timedelta(minutes=5),
        severity=None,
    )
    make_analysis_job(
        db,
        group,
        status=AnalysisJobStatus.FAILED.value,
        requested_at=NOW - timedelta(minutes=1),
        severity=None,
    )

    active = service.active_analysis_by_fingerprint(db, ["fp-timeout"])
    assert active["fp-timeout"].id == running.id

    # 최신 1 건 조회는 (의도대로) 실패한 쪽을 준다 — 두 조회의 역할이 다르다.
    latest_job, _ = service.latest_analysis_by_fingerprint(db, ["fp-timeout"])["fp-timeout"]
    assert latest_job.id != running.id
