"""대시보드 개요 — metric 기반 추이 + DB 기반 상위 그룹.

Phase 1 담당 트랙: **정책 API**

계약: 시간대별 오류 건수는 **저장 데이터가 아니라 `count_over_time`** 으로 구한다.
라인 조회에는 상한이 걸려 있어 오류 폭증 시 실제보다 적게 나오기 때문이다.
상위 그룹은 이미 저장된 그룹화 결과에서 집계하고, 분석 상태는 fingerprint 기준이다.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from app.enums import AnalysisJobStatus, QueryRunStatus, Severity
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
    session_factory,
)


def _overview(client, provider: FakeLogSource, **params):
    with patch("app.policies.integrations.build_provider", return_value=provider):
        return client.get("/api/dashboard/overview", params=params)


def test_overview_uses_count_over_time_for_the_series(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    # 저장된 그룹 건수(1000)와 metric 건수(12)가 다르다 — 시리즈는 metric 쪽이어야 한다.
    make_error_group(db, run, fingerprint="fp-1", count=1000)
    provider = FakeLogSource(
        count_series=count_series(("payment-api", 7.0), ("auth-api", 5.0))
    )

    response = _overview(client, provider, query_run_id=run.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["policy_id"] == policy.id
    assert body["query_run_id"] == run.id
    assert body["total_errors"] == 12.0
    assert len(body["series"]) == 2
    assert {item["service"]: item["count"] for item in body["by_service"]} == {
        "payment-api": 7.0,
        "auth-api": 5.0,
    }

    query, time_range, step = provider.count_calls[0]
    assert query == policy.logql
    assert step == 300
    assert time_range.duration == timedelta(minutes=60)


def test_overview_top_groups_come_from_the_database(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    make_error_group(db, run, fingerprint="fp-small", count=3)
    big = make_error_group(db, run, fingerprint="fp-big", count=42)
    job = make_analysis_job(db, big, severity=Severity.CRITICAL.value)

    body = _overview(client, FakeLogSource(), query_run_id=run.id, top=1).json()

    assert [item["fingerprint"] for item in body["top_groups"]] == ["fp-big"]
    top = body["top_groups"][0]
    assert top["count"] == 42
    # 분석 상태는 fingerprint 기준으로 붙는다.
    assert top["analysis_status"] == AnalysisJobStatus.SUCCEEDED.value
    assert top["latest_analysis_job_id"] == job.id
    assert top["latest_severity"] == Severity.CRITICAL.value


def test_overview_by_policy_picks_the_latest_succeeded_run(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    old = make_query_run(db, policy, started_at=NOW - timedelta(hours=3))
    make_error_group(db, old, fingerprint="fp-old", count=5)
    newest = make_query_run(db, policy, started_at=NOW)
    make_error_group(db, newest, fingerprint="fp-new", count=9)
    make_query_run(db, policy, started_at=NOW, status=QueryRunStatus.FAILED.value)

    body = _overview(client, FakeLogSource(), policy_id=policy.id).json()

    assert body["query_run_id"] == newest.id
    assert [item["fingerprint"] for item in body["top_groups"]] == ["fp-new"]


def test_overview_honours_explicit_range(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    provider = FakeLogSource()

    response = _overview(
        client,
        provider,
        query_run_id=run.id,
        range_start=(NOW - timedelta(minutes=15)).isoformat(),
        range_end=NOW.isoformat(),
        step_seconds=60,
    )

    assert response.status_code == 200
    _, time_range, step = provider.count_calls[0]
    assert time_range.duration == timedelta(minutes=15)
    assert step == 60


def test_overview_clamps_range_to_server_ceiling(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    provider = FakeLogSource()

    body = _overview(
        client,
        provider,
        query_run_id=run.id,
        range_start=(NOW - timedelta(days=30)).isoformat(),
        range_end=NOW.isoformat(),
    ).json()

    assert "range_clamped" in {warning["code"] for warning in body["warnings"]}
    assert provider.count_calls[0][1].duration == timedelta(days=1)


def test_metric_query_failure_degrades_to_a_warning(client, db) -> None:
    """추이가 실패해도 DB 기반 상위 그룹은 그대로 보여준다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    make_error_group(db, run, fingerprint="fp-1", count=4)
    provider = FakeLogSource(count_error=LogSourceError("Loki 503"))

    body = _overview(client, provider, query_run_id=run.id).json()

    assert {warning["code"] for warning in body["warnings"]} == {"count_query_failed"}
    assert "Loki 503" in body["warnings"][0]["message"]
    assert body["series"] == []
    assert body["total_errors"] == 0.0
    assert [item["fingerprint"] for item in body["top_groups"]] == ["fp-1"]
    # metric 이 없으면 서비스별 건수는 저장된 그룹으로 대체한다.
    assert body["by_service"] == [{"service": "payment-api", "count": 4.0}]


def test_adapter_without_count_support_is_reported(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    provider = FakeLogSource(supports_count=False)

    body = _overview(client, provider, query_run_id=run.id).json()

    assert "count_unsupported" in {warning["code"] for warning in body["warnings"]}
    assert provider.count_calls == []


def test_overview_without_any_policy_or_run(client) -> None:
    response = client.get("/api/dashboard/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["policy_id"] is None
    assert body["query_run_id"] is None
    assert body["top_groups"] == []
    assert "no_policy" in {warning["code"] for warning in body["warnings"]}


def test_overview_unknown_ids_are_404(client) -> None:
    assert client.get("/api/dashboard/overview", params={"policy_id": 999}).status_code == 404
    assert client.get("/api/dashboard/overview", params={"query_run_id": 999}).status_code == 404


def test_inactive_connection_is_reported(client, db) -> None:
    connection = make_connection(db, active=False)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)

    body = client.get("/api/dashboard/overview", params={"query_run_id": run.id}).json()
    assert "connection_unavailable" in {warning["code"] for warning in body["warnings"]}
