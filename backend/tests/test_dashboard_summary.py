"""`GET /api/dashboard/summary` — 통합 대시보드 (Phase 5).

`overview` 와 달리 **모든 정책**의 상태를 한 줄씩 준다. 계약이 걸린 지점:
- `total_errors_24h` 는 `count_over_time` 이며, 실패하면 **0 이 아니라 null** 이다.
- `unanalyzed_group_count` 는 **최근 성공 회차**의 그룹 중 fingerprint 분석 이력이
  전혀 없는 수다 (그룹 id 기준이 아니다).
- 정책 하나의 metric 실패가 나머지 정책 줄을 죽이지 않는다.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from app.dashboard import summary as summary_service
from app.enums import AnalysisJobStatus, QueryRunStatus
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


def get_summary(client, provider=None):
    source = provider if provider is not None else FakeLogSource(count_series=count_series())
    with patch("app.policies.integrations.build_provider", return_value=source):
        return client.get("/api/dashboard/summary")


def codes(row) -> set[str]:
    return {warning["code"] for warning in row["warnings"]}


def test_summary_lists_every_policy_with_its_schedule(client, db) -> None:
    connection = make_connection(db)
    first = make_policy(
        db,
        connection,
        name="scheduled",
        schedule_enabled=True,
        schedule_interval_minutes=15,
    )
    second = make_policy(db, connection, name="manual-only", active=False)

    body = get_summary(client).json()

    assert body["generated_at"]
    assert [row["policy_id"] for row in body["policies"]] == [first.id, second.id]
    assert body["policies"][0]["name"] == "scheduled"
    assert body["policies"][0]["active"] is True
    assert body["policies"][0]["schedule_enabled"] is True
    assert body["policies"][0]["schedule_interval_minutes"] == 15
    # 비활성 정책도 목록에서 빼지 않는다 — 빼면 "지웠나?" 와 구분되지 않는다.
    assert body["policies"][1]["active"] is False
    assert body["policies"][1]["schedule_enabled"] is False


def test_policy_without_runs_has_no_last_run(client, db) -> None:
    connection = make_connection(db)
    make_policy(db, connection)

    row = get_summary(client).json()["policies"][0]

    assert row["last_run"] is None
    assert row["unanalyzed_group_count"] == 0
    assert summary_service.WARN_NO_SUCCESSFUL_RUN in codes(row)


def test_last_run_carries_status_counts_and_warnings(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    run.fetched_count = 42
    run.warnings = [{"code": "range_clamped", "message": "조정됨", "count": 120}]
    db.add(run)
    db.commit()
    make_error_group(db, run, fingerprint="fp-1")
    make_error_group(db, run, fingerprint="fp-2")

    row = get_summary(client).json()["policies"][0]

    assert row["last_run"]["id"] == run.id
    assert row["last_run"]["status"] == QueryRunStatus.SUCCEEDED.value
    assert row["last_run"]["fetched_count"] == 42
    assert row["last_run"]["group_count"] == 2
    assert row["last_run"]["warnings"][0]["code"] == "range_clamped"


def test_last_run_is_the_latest_even_when_it_failed(client, db) -> None:
    """가장 최근 회차가 실패면 그 실패를 보여준다 (성공만 보여주면 장애가 안 보인다)."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    make_query_run(db, policy, started_at=NOW - timedelta(hours=2))
    failed = make_query_run(db, policy, status=QueryRunStatus.FAILED.value, started_at=NOW)

    row = get_summary(client).json()["policies"][0]

    assert row["last_run"]["id"] == failed.id
    assert row["last_run"]["status"] == QueryRunStatus.FAILED.value


def test_unanalyzed_count_uses_the_latest_successful_run(client, db) -> None:
    """실패 회차에는 그룹이 없다 — 미분석 수는 마지막 **성공** 회차에서 센다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    succeeded = make_query_run(db, policy, started_at=NOW - timedelta(hours=2))
    make_error_group(db, succeeded, fingerprint="fp-1")
    make_error_group(db, succeeded, fingerprint="fp-2")
    make_query_run(db, policy, status=QueryRunStatus.FAILED.value, started_at=NOW)

    row = get_summary(client).json()["policies"][0]

    assert row["unanalyzed_group_count"] == 2
    assert summary_service.WARN_NO_SUCCESSFUL_RUN not in codes(row)


def test_unanalyzed_count_is_fingerprint_based_not_group_id_based(client, db) -> None:
    """어제 분석한 오류가 오늘 새 그룹 id 로 나타나도 "미분석" 이 아니다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)

    old_run = make_query_run(db, policy, started_at=NOW - timedelta(days=1))
    old_group = make_error_group(db, old_run, fingerprint="fp-known")
    make_analysis_job(db, old_group, fingerprint="fp-known")

    new_run = make_query_run(db, policy, started_at=NOW)
    make_error_group(db, new_run, fingerprint="fp-known")  # 같은 오류, 새 그룹 id
    make_error_group(db, new_run, fingerprint="fp-fresh")

    row = get_summary(client).json()["policies"][0]

    assert row["unanalyzed_group_count"] == 1


def test_a_failed_analysis_still_counts_as_analyzed(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-1")
    make_analysis_job(db, group, status=AnalysisJobStatus.FAILED.value, severity=None)

    row = get_summary(client).json()["policies"][0]

    assert row["unanalyzed_group_count"] == 0


# ------------------------------------------------------------- 24h 건수


def test_total_errors_24h_comes_from_count_over_time(client, db) -> None:
    connection = make_connection(db)
    make_policy(db, connection)
    source = FakeLogSource(count_series=count_series(("payment-api", 7.0), ("auth-api", 3.0)))

    row = get_summary(client, source).json()["policies"][0]

    assert row["total_errors_24h"] == 10.0
    query, time_range, step = source.count_calls[0]
    assert (time_range.end - time_range.start) == summary_service.SUMMARY_RANGE
    assert step == summary_service.SUMMARY_STEP_SECONDS


def test_count_failure_yields_null_not_zero(client, db) -> None:
    """0 은 "오류가 없었다" 로 읽힌다 — 조회 실패는 null 이어야 한다."""
    connection = make_connection(db)
    make_policy(db, connection)
    source = FakeLogSource(count_error=LogSourceError("Loki 502"))

    row = get_summary(client, source).json()["policies"][0]

    assert row["total_errors_24h"] is None
    assert summary_service.WARN_COUNT_FAILED in codes(row)


def test_one_broken_policy_does_not_kill_the_other_rows(client, db) -> None:
    connection = make_connection(db)
    broken = make_policy(db, connection, name="broken")
    healthy = make_policy(db, connection, name="healthy")

    def _build(conn, _seen=[]):  # noqa: B006 - 호출 순서로 갈라 준다
        _seen.append(conn)
        if len(_seen) == 1:
            return FakeLogSource(count_error=LogSourceError("Loki 502"))
        return FakeLogSource(count_series=count_series(("payment-api", 5.0)))

    with patch("app.policies.integrations.build_provider", side_effect=_build):
        body = client.get("/api/dashboard/summary").json()

    rows = {row["policy_id"]: row for row in body["policies"]}
    assert rows[broken.id]["total_errors_24h"] is None
    assert rows[healthy.id]["total_errors_24h"] == 5.0


def test_inactive_policy_is_not_queried(client, db) -> None:
    connection = make_connection(db)
    make_policy(db, connection, active=False)
    source = FakeLogSource(count_series=count_series(("payment-api", 5.0)))

    row = get_summary(client, source).json()["policies"][0]

    assert source.count_calls == []
    assert row["total_errors_24h"] is None
    assert summary_service.WARN_POLICY_INACTIVE in codes(row)


def test_inactive_connection_is_reported_not_queried(client, db) -> None:
    connection = make_connection(db, active=False)
    make_policy(db, connection)
    source = FakeLogSource(count_series=count_series(("payment-api", 5.0)))

    row = get_summary(client, source).json()["policies"][0]

    assert source.count_calls == []
    assert row["total_errors_24h"] is None
    assert summary_service.WARN_CONNECTION_UNAVAILABLE in codes(row)


def test_adapter_without_count_support_is_reported(client, db) -> None:
    connection = make_connection(db)
    make_policy(db, connection)
    source = FakeLogSource(supports_count=False)

    row = get_summary(client, source).json()["policies"][0]

    assert row["total_errors_24h"] is None
    assert summary_service.WARN_COUNT_UNSUPPORTED in codes(row)


def test_a_slow_policy_is_dropped_by_the_per_policy_timeout(client, db) -> None:
    """정책 하나가 느리다고 화면 전체가 붙잡히지 않는다."""
    import time

    connection = make_connection(db)
    make_policy(db, connection)

    class SlowSource(FakeLogSource):
        def count_over_time(self, query, range, step):  # noqa: A002
            time.sleep(0.5)
            return count_series(("payment-api", 1.0))

    with patch.object(summary_service, "SUMMARY_COUNT_TIMEOUT_SECONDS", 0.05):
        row = get_summary(client, SlowSource()).json()["policies"][0]

    assert row["total_errors_24h"] is None
    assert summary_service.WARN_COUNT_TIMEOUT in codes(row)


# ------------------------------------------------------- 24h 시계열 (Phase 6)


def test_series_24h_reuses_the_points_of_the_same_count_query(client, db) -> None:
    """추가 Loki 호출 없이, 합계를 만든 그 응답의 포인트를 그대로 싣는다."""
    connection = make_connection(db)
    make_policy(db, connection)
    source = FakeLogSource(
        count_series=count_series(("payment-api", 7.0), ("payment-api", 3.0))
    )

    row = get_summary(client, source).json()["policies"][0]

    assert len(source.count_calls) == 1, "시계열 때문에 metric 을 두 번 부르면 안 된다"
    assert row["total_errors_24h"] == 10.0
    assert [point["value"] for point in row["series_24h"]] == [3.0, 7.0]
    assert [point["timestamp"] for point in row["series_24h"]] == sorted(
        point["timestamp"] for point in row["series_24h"]
    )
    assert sum(point["value"] for point in row["series_24h"]) == row["total_errors_24h"]


def test_series_24h_folds_multiple_series_at_the_same_timestamp(client, db) -> None:
    """`sum by (service)` 라 한 시각에 점이 여러 개 온다 — 카드 차트는 그 합을 그린다."""
    connection = make_connection(db)
    make_policy(db, connection)
    moment = NOW - timedelta(hours=1)
    source = FakeLogSource(
        count_series=CountSeries(
            step_seconds=summary_service.SUMMARY_STEP_SECONDS,
            points=[
                CountPoint(timestamp=moment, value=4.0, labels={"service": "payment-api"}),
                CountPoint(timestamp=moment, value=6.0, labels={"service": "auth-api"}),
                CountPoint(timestamp=NOW, value=1.0, labels={"service": "auth-api"}),
            ],
        )
    )

    row = get_summary(client, source).json()["policies"][0]

    assert [point["value"] for point in row["series_24h"]] == [10.0, 1.0]
    assert row["total_errors_24h"] == 11.0


def test_series_24h_is_empty_when_the_count_query_fails(client, db) -> None:
    """빈 배열 + null 합계 + 경고 — "0 건" 으로 보이는 선을 그리지 않는다."""
    connection = make_connection(db)
    make_policy(db, connection)
    source = FakeLogSource(count_error=LogSourceError("Loki 502"))

    row = get_summary(client, source).json()["policies"][0]

    assert row["series_24h"] == []
    assert row["total_errors_24h"] is None


def test_series_24h_is_empty_for_an_inactive_policy(client, db) -> None:
    connection = make_connection(db)
    make_policy(db, connection, active=False)

    row = get_summary(client, FakeLogSource(count_series=count_series())).json()["policies"][0]

    assert row["series_24h"] == []


def test_summary_has_no_policies_when_none_exist(client) -> None:
    body = get_summary(client).json()
    assert body["policies"] == []
    assert body["generated_at"]


def test_overview_still_works_alongside_summary(client, db) -> None:
    """정책 상세 뷰용 `/overview` 는 그대로 유지된다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    make_query_run(db, policy)
    source = FakeLogSource(count_series=count_series(("payment-api", 2.0)))
    with patch("app.policies.integrations.build_provider", return_value=source):
        response = client.get("/api/dashboard/overview", params={"policy_id": policy.id})
    assert response.status_code == 200
    assert response.json()["policy_id"] == policy.id
