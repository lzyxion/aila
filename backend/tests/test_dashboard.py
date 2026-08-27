"""대시보드 개요 — metric 기반 추이 + DB 기반 상위 그룹.

Phase 1 담당 트랙: **정책 API**

계약: 시간대별 오류 건수는 **저장 데이터가 아니라 `count_over_time`** 으로 구한다.
라인 조회에는 상한이 걸려 있어 오류 폭증 시 실제보다 적게 나오기 때문이다.
상위 그룹은 이미 저장된 그룹화 결과에서 집계하고, 분석 상태는 fingerprint 기준이다.
"""

from __future__ import annotations

from datetime import UTC, timedelta
from unittest.mock import patch

from app.enums import AnalysisJobStatus, QueryRunStatus, Severity
from app.providers.logsource import LogSourceError
from app.schemas.logrecord import CountPoint, CountSeries
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

BASELINE_QUERY = '{service="payment-api"}'


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
    assert query == policy.query
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

    codes = {warning["code"] for warning in body["warnings"]}
    assert codes == {"count_query_failed", "by_service_from_lines"}
    failure = next(w for w in body["warnings"] if w["code"] == "count_query_failed")
    assert "Loki 503" in failure["message"]
    assert body["series"] == []
    assert body["total_errors"] == 0.0
    assert [item["fingerprint"] for item in body["top_groups"]] == ["fp-1"]
    # metric 이 없으면 서비스별 건수는 저장된 그룹으로 대체한다 — 조용히가 아니라 경고와 함께.
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


# ------------------------------------------------- 서비스별 건수는 metric 기준


def test_by_service_uses_the_mapped_source_label(client, db) -> None:
    """어댑터는 `sum by (<소스 라벨>)` 로 감싼다 — 그 이름으로 시리즈를 읽어야 한다.

    v1 은 라벨 없는 `sum(...)` 이라 시리즈 라벨이 늘 비었고, 대시보드는 조용히
    DB(조회 상한에 잘린 라인) 집계로 폴백했다. 오류가 폭증하는 순간 — 정확히 이
    화면이 필요한 순간 — 실제보다 적은 숫자를 보여주는 상태였다.
    """
    connection = make_connection(db)  # label_mapping = {"app": "service"}
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    make_error_group(db, run, fingerprint="fp-1", count=999)

    series = count_series(("payment-api", 7.0), ("auth-api", 5.0))
    for point in series.points:
        point.labels = {"app": point.labels["service"]}  # 소스 라벨 이름으로 온다
    provider = FakeLogSource(count_series=series)
    provider.service_label = "app"

    body = _overview(client, provider, query_run_id=run.id).json()

    assert {item["service"]: item["count"] for item in body["by_service"]} == {
        "payment-api": 7.0,
        "auth-api": 5.0,
    }
    # 폴백하지 않았으므로 경고도 없다.
    assert "by_service_from_lines" not in {w["code"] for w in body["warnings"]}


def test_db_fallback_for_by_service_is_reported(client, db) -> None:
    """폴백이 일어나면 조용히 넘어가지 않고 응답에 사유를 남긴다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    make_error_group(db, run, fingerprint="fp-1", count=4)
    # 라벨 없는 시리즈 (= v1 의 `sum(...)` 결과 모양).
    series = count_series(("payment-api", 3.0))
    series.points[0].labels = {}

    body = _overview(client, FakeLogSource(count_series=series), query_run_id=run.id).json()

    assert "by_service_from_lines" in {w["code"] for w in body["warnings"]}
    assert body["by_service"] == [{"service": "payment-api", "count": 4.0}]


# ------------------------------------------------------------ step 과 기간


def test_range_end_is_ceiled_to_the_step_boundary(client, db) -> None:
    """step 경계에 걸치지 않는 `range_end` 는 마지막 버킷을 영구히 잃는다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    # range_end 가 300 초 배수가 아니게 어긋난 조회 이력.
    run = make_query_run(db, policy, started_at=NOW - timedelta(seconds=137))
    provider = FakeLogSource()

    body = _overview(client, provider, query_run_id=run.id).json()

    _, time_range, step = provider.count_calls[0]
    assert step == 300
    # SQLite 는 tz 를 버리고 돌려준다.
    assert time_range.end > run.range_end.replace(tzinfo=UTC)
    assert int(time_range.end.timestamp()) % step == 0
    # 응답에 실린 기간도 실제로 조회한 기간과 같아야 한다.
    assert body["range_end"].startswith(time_range.end.isoformat()[:19])


def test_step_is_raised_when_there_would_be_too_many_points(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    provider = FakeLogSource()

    body = _overview(
        client,
        provider,
        query_run_id=run.id,
        range_start=(NOW - timedelta(days=1)).isoformat(),
        range_end=NOW.isoformat(),
        step_seconds=15,
    ).json()

    raised = next(w for w in body["warnings"] if w["code"] == "step_raised")
    _, time_range, step = provider.count_calls[0]
    assert step > 15
    assert raised["count"] == step
    assert (time_range.end - time_range.start).total_seconds() / step <= 1000


def test_step_bounds_are_enforced_by_the_router(client, db) -> None:
    assert client.get("/api/dashboard/overview", params={"step_seconds": 1}).status_code == 422
    assert (
        client.get("/api/dashboard/overview", params={"step_seconds": 7200}).status_code == 422
    )


# ------------------------------------------------- Phase 7: 시각별 합산 시리즈


def _two_services_at_one_moment() -> CountSeries:
    """`sum by (service)` 가 한 시각에 서비스 수만큼 점을 주는 실제 모양."""
    moment = NOW - timedelta(minutes=5)
    return CountSeries(
        step_seconds=300,
        points=[
            CountPoint(timestamp=moment, value=7.0, labels={"service": "payment-api"}),
            CountPoint(timestamp=moment, value=5.0, labels={"service": "auth-api"}),
            CountPoint(timestamp=NOW, value=2.0, labels={"service": "payment-api"}),
        ],
    )


def test_series_is_folded_by_timestamp(client, db) -> None:
    """같은 시각의 점이 여러 번 실리면 차트가 그 시각을 여러 번 그린다.

    summary 카드는 Phase 6 부터 접어서 실었는데 overview 는 접지 않아, 같은 데이터가
    화면 두 곳에서 다른 모양으로 보였다.
    """
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)

    body = _overview(
        client, FakeLogSource(count_series=_two_services_at_one_moment()), query_run_id=run.id
    ).json()

    timestamps = [point["timestamp"] for point in body["series"]]
    assert len(timestamps) == len(set(timestamps)) == 2
    # 접은 값의 합 = 총 건수 ("선 아래 면적 = 총 건수" 가 성립해야 한다).
    assert sum(point["value"] for point in body["series"]) == body["total_errors"] == 14.0
    assert body["series"][0]["value"] == 12.0
    # 접힌 점에는 라벨을 싣지 않는다 (서비스 분해는 by_service 의 몫).
    assert body["series"][0]["labels"] == {}


def test_folding_happens_after_by_service_is_computed(client, db) -> None:
    """접기는 라벨을 버린다 — 먼저 접으면 서비스별 건수가 통째로 사라진다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    make_error_group(db, run, fingerprint="fp-1", count=99)

    body = _overview(
        client, FakeLogSource(count_series=_two_services_at_one_moment()), query_run_id=run.id
    ).json()

    assert {item["service"]: item["count"] for item in body["by_service"]} == {
        "payment-api": 9.0,
        "auth-api": 5.0,
    }
    # 라벨이 살아 있었으므로 DB 폴백은 일어나지 않는다.
    assert "by_service_from_lines" not in {w["code"] for w in body["warnings"]}


# ------------------------------------------------ Phase 7: 회차 전체 COUNT


def test_group_counts_are_totals_not_the_top_n(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    analyzed = make_error_group(db, run, fingerprint="fp-1", count=10)
    make_error_group(db, run, fingerprint="fp-2", count=9)
    make_error_group(db, run, fingerprint="fp-3", count=8)
    make_analysis_job(db, analyzed)

    body = _overview(client, FakeLogSource(), query_run_id=run.id, top=1).json()

    assert len(body["top_groups"]) == 1  # 상위 N 은 목록일 뿐이다
    assert body["group_count"] == 3
    assert body["unanalyzed_group_count"] == 2


def test_failed_analysis_still_counts_as_analysed(client, db) -> None:
    """실패를 미분석으로 세면 같은 실패를 매 회차 다시 태우게 된다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-1", count=3)
    make_analysis_job(db, group, status=AnalysisJobStatus.FAILED.value, severity=None)

    body = _overview(client, FakeLogSource(), query_run_id=run.id).json()

    assert body["group_count"] == 1
    assert body["unanalyzed_group_count"] == 0


def test_group_counts_are_null_without_a_run(client, db) -> None:
    """0 으로 적으면 "그룹이 없었다" 로 읽혀 "아직 한 번도 돌지 않았다" 와 구분되지 않는다."""
    body = client.get("/api/dashboard/overview").json()

    assert body["group_count"] is None
    assert body["unanalyzed_group_count"] is None


# --------------------------------------------- Phase 7: 유입량 · 오류 비율


def _provider_with_baseline(errors: CountSeries, ingest: CountSeries | None) -> FakeLogSource:
    provider = FakeLogSource(count_series=errors)
    if ingest is not None:
        provider.count_series_by_query = {BASELINE_QUERY: ingest}
    return provider


def test_baseline_query_yields_ingest_total_and_ratio(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection, baseline_query=BASELINE_QUERY)
    run = make_query_run(db, policy)
    ingest = CountSeries(
        step_seconds=300,
        points=[
            CountPoint(timestamp=NOW, value=300.0, labels={"service": "payment-api"}),
            CountPoint(timestamp=NOW, value=100.0, labels={"service": "auth-api"}),
        ],
    )
    provider = _provider_with_baseline(count_series(("payment-api", 12.0)), ingest)

    body = _overview(client, provider, query_run_id=run.id).json()

    assert body["ingest_total"] == 400.0
    assert body["error_ratio"] == 0.03
    # 분모 시리즈도 시각별로 접혀 나간다.
    assert body["ingest_series"] == [
        {"timestamp": body["ingest_series"][0]["timestamp"], "value": 400.0, "labels": {}}
    ]

    # 오류 쿼리와 **같은 기간·step** 으로 정확히 한 번 더 부른다.
    assert [call[0] for call in provider.count_calls] == [policy.query, BASELINE_QUERY]
    assert provider.count_calls[0][1] == provider.count_calls[1][1]
    assert provider.count_calls[0][2] == provider.count_calls[1][2]


def test_ratio_is_null_when_the_denominator_is_zero(client, db) -> None:
    """0 으로 나눌 수 없다 — 비율은 null 이고, 유입 0 자체는 사실이므로 그대로 싣는다."""
    connection = make_connection(db)
    policy = make_policy(db, connection, baseline_query=BASELINE_QUERY)
    run = make_query_run(db, policy)
    provider = _provider_with_baseline(
        count_series(("payment-api", 4.0)), CountSeries(step_seconds=300)
    )

    body = _overview(client, provider, query_run_id=run.id).json()

    assert body["ingest_total"] == 0.0
    assert body["ingest_series"] == []
    assert body["error_ratio"] is None


def test_baseline_failure_degrades_to_a_warning(client, db) -> None:
    """분모 하나가 죽었다고 추이·상위 그룹까지 사라지면 안 된다."""
    connection = make_connection(db)
    policy = make_policy(db, connection, baseline_query=BASELINE_QUERY)
    run = make_query_run(db, policy)
    make_error_group(db, run, fingerprint="fp-1", count=4)
    provider = FakeLogSource(count_error=LogSourceError("Loki 503"))
    # 오류 쿼리만 성공시키고 분모 쿼리는 count_error 로 떨어뜨린다.
    provider.count_series_by_query = {policy.query: count_series(("payment-api", 6.0))}

    body = _overview(client, provider, query_run_id=run.id).json()

    assert "baseline_query_failed" in {w["code"] for w in body["warnings"]}
    assert body["ingest_total"] is None  # 0 이 아니다 — 0 은 "유입이 없었다" 로 읽힌다
    assert body["ingest_series"] == []
    assert body["error_ratio"] is None
    assert body["total_errors"] == 6.0
    assert [item["fingerprint"] for item in body["top_groups"]] == ["fp-1"]


def test_without_baseline_query_no_extra_metric_call(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)  # baseline_query 없음
    run = make_query_run(db, policy)
    provider = FakeLogSource(count_series=count_series(("payment-api", 3.0)))

    body = _overview(client, provider, query_run_id=run.id).json()

    assert len(provider.count_calls) == 1
    assert body["ingest_total"] is None
    assert body["ingest_series"] == []
    assert body["error_ratio"] is None
    assert "baseline_query_failed" not in {w["code"] for w in body["warnings"]}


def test_baseline_is_skipped_when_the_adapter_cannot_count(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection, baseline_query=BASELINE_QUERY)
    run = make_query_run(db, policy)

    body = _overview(
        client, FakeLogSource(supports_count=False), query_run_id=run.id
    ).json()

    codes = {w["code"] for w in body["warnings"]}
    assert {"count_unsupported", "baseline_query_failed"} <= codes
    assert body["ingest_total"] is None
    assert body["error_ratio"] is None
