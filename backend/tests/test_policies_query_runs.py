"""정책 실행(`POST /policies/{id}/query-runs`) 흐름.

Phase 1 담당 트랙: **정책 API**

그룹화·마스킹 트랙(`group_records`)과 Loki 어댑터 트랙(`build_provider`)이 동시에
구현 중이므로 둘 다 mock 한다. 여기서 검증하는 것은 **정책 API 가 책임지는 부분**이다 —
한도 clamp, 제외 정규식 필터, 그룹 저장, 실패 시 이력 보존.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.enums import QueryRunStatus
from app.models import ErrorGroup, ErrorSample, QueryRun
from app.providers.logsource import LogSourceError
from app.schemas.logrecord import FetchResult, FetchWarning
from tests.test_policies_fixtures import (  # noqa: F401 - fixture 재수출
    NOW,
    FakeLogSource,
    client,
    db,
    engine,
    grouped_error,
    grouped_sample,
    log_record,
    make_connection,
    make_policy,
    no_real_log_source,
    session_factory,
)


def _run(client, policy_id: int, provider: FakeLogSource, groups=None, **body):
    """build_provider / group_records 를 mock 한 채 정책을 실행한다."""
    with (
        patch("app.policies.integrations.build_provider", return_value=provider) as build,
        patch(
            "app.policies.integrations.group_records", return_value=list(groups or [])
        ) as group,
    ):
        response = client.post(f"/api/policies/{policy_id}/query-runs", json=body)
    return response, build, group


def _warning_codes(payload: dict) -> set[str]:
    return {warning["code"] for warning in payload["warnings"]}


# ------------------------------------------------------------------- 성공


def test_query_run_persists_groups_and_samples(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    provider = FakeLogSource(
        fetch_result=FetchResult(
            records=[log_record("TimeoutError: boom"), log_record("DatabaseConnectionError")],
            fetched=2,
            dropped=1,
            warnings=[FetchWarning(code="parse_error", message="| json 파싱 실패", count=1)],
        )
    )
    groups = [
        grouped_error("fp-timeout", count=7, samples=[grouped_sample("a"), grouped_sample("b")]),
        grouped_error("fp-db", count=2, samples=[grouped_sample("c")]),
    ]

    response, build, group = _run(client, policy.id, provider, groups)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == QueryRunStatus.SUCCEEDED.value
    assert body["group_count"] == 2
    assert body["fetched_count"] == 2
    assert body["dropped_count"] == 1
    assert body["error_message"] is None
    assert "parse_error" in _warning_codes(body)

    # 어댑터·그룹화 계약대로 호출했는가
    build.assert_called_once()
    assert build.call_args.args[0].id == connection.id
    assert group.call_args.kwargs == {
        "max_samples_per_group": policy.max_samples_per_group,
        # exclusions 는 마스킹 추가 패턴이 아니라 라인 제거 필터다.
        "extra_mask_patterns": (),
    }
    assert provider.fetch_calls[0][0] == policy.logql

    stored = db.query(ErrorGroup).filter_by(query_run_id=body["id"]).all()
    assert {group_row.fingerprint for group_row in stored} == {"fp-timeout", "fp-db"}
    assert db.query(ErrorSample).count() == 3
    assert {sample.masked_log for sample in db.query(ErrorSample).all()} == {"a", "b", "c"}


def test_samples_are_capped_by_policy(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection, max_samples_per_group=2, name="capped")
    groups = [
        grouped_error(
            "fp-1",
            samples=[grouped_sample("a"), grouped_sample("b"), grouped_sample("c")],
        )
    ]
    provider = FakeLogSource(fetch_result=FetchResult(records=[log_record("x")]))

    response, _, _ = _run(client, policy.id, provider, groups)

    assert response.status_code == 201
    assert db.query(ErrorSample).count() == 2


def test_get_query_run(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    provider = FakeLogSource(fetch_result=FetchResult(records=[log_record("x")]))
    created, _, _ = _run(client, policy.id, provider, [grouped_error("fp-1")])
    run_id = created.json()["id"]

    response = client.get(f"/api/query-runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["group_count"] == 1
    assert client.get("/api/query-runs/9999").status_code == 404


# ------------------------------------------------------------- 한도 clamp


def test_range_is_clamped_to_policy_limit(client, db) -> None:
    """정책 한도를 넘는 기간은 422 가 아니라 clamp 하고, 조정 사실을 응답에 남긴다."""
    connection = make_connection(db)
    policy = make_policy(db, connection, default_range_minutes=60)
    provider = FakeLogSource(fetch_result=FetchResult())

    response, _, _ = _run(
        client,
        policy.id,
        provider,
        [],
        range_start=(NOW - timedelta(days=1)).isoformat(),
        range_end=NOW.isoformat(),
    )

    assert response.status_code == 201
    body = response.json()
    assert "range_clamped" in _warning_codes(body)
    assert datetime.fromisoformat(body["range_start"]).replace(tzinfo=None) == (
        NOW - timedelta(minutes=60)
    ).replace(tzinfo=None)

    # 어댑터에도 clamp 된 구간이 그대로 넘어간다.
    _, time_range, _ = provider.fetch_calls[0]
    assert time_range.duration == timedelta(minutes=60)


def test_range_within_limit_is_untouched(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection, default_range_minutes=120)
    provider = FakeLogSource(fetch_result=FetchResult())

    response, _, _ = _run(
        client,
        policy.id,
        provider,
        [],
        range_start=(NOW - timedelta(minutes=30)).isoformat(),
        range_end=NOW.isoformat(),
    )

    assert "range_clamped" not in _warning_codes(response.json())
    assert provider.fetch_calls[0][1].duration == timedelta(minutes=30)


def test_limit_is_clamped_to_policy_max_lines(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection, max_lines=100)
    provider = FakeLogSource(fetch_result=FetchResult())

    response, _, _ = _run(client, policy.id, provider, [], limit=9999)

    assert "limit_clamped" in _warning_codes(response.json())
    assert provider.fetch_calls[0][2] == 100


def test_limit_defaults_to_policy_max_lines(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection, max_lines=250)
    provider = FakeLogSource(fetch_result=FetchResult())

    _run(client, policy.id, provider, [])

    assert provider.fetch_calls[0][2] == 250


def test_inverted_range_is_rejected(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    provider = FakeLogSource(fetch_result=FetchResult())

    response, _, _ = _run(
        client,
        policy.id,
        provider,
        [],
        range_start=NOW.isoformat(),
        range_end=(NOW - timedelta(minutes=10)).isoformat(),
    )
    assert response.status_code == 422


# --------------------------------------------------------- exclusions 필터


def test_exclusions_remove_matching_lines(client, db) -> None:
    """exclusions 는 마스킹 추가 패턴이 아니라 조회 결과에서 라인을 빼는 필터다."""
    connection = make_connection(db)
    policy = make_policy(db, connection, exclusions=[r"healthcheck", r"^DEBUG"])
    provider = FakeLogSource(
        fetch_result=FetchResult(
            records=[
                log_record("TimeoutError: boom"),
                log_record("GET /healthcheck 200"),
                log_record("DEBUG cache warm"),
                log_record("DatabaseConnectionError"),
            ],
            fetched=4,
        )
    )

    response, _, group = _run(client, policy.id, provider, [])

    assert response.status_code == 201
    body = response.json()
    assert "excluded_by_policy" in _warning_codes(body)
    assert body["fetched_count"] == 4
    assert body["dropped_count"] == 2

    passed_records = group.call_args.args[0]
    assert [record.message for record in passed_records] == [
        "TimeoutError: boom",
        "DatabaseConnectionError",
    ]


def test_no_exclusions_passes_everything_through(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection, exclusions=[])
    provider = FakeLogSource(
        fetch_result=FetchResult(records=[log_record("a"), log_record("b")], fetched=2)
    )

    response, _, group = _run(client, policy.id, provider, [])

    assert response.json()["dropped_count"] == 0
    assert len(group.call_args.args[0]) == 2


def test_truncated_result_is_warned(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    provider = FakeLogSource(
        fetch_result=FetchResult(records=[log_record("a")], fetched=1, truncated=True)
    )

    response, _, _ = _run(client, policy.id, provider, [])
    assert "limit_reached" in _warning_codes(response.json())


# ------------------------------------------------------------------- 실패


def test_failed_fetch_records_the_run(client, db) -> None:
    """실패해도 조회 이력은 남는다 — 왜 비었는지 나중에 확인할 수 있어야 한다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    provider = FakeLogSource(fetch_error=LogSourceError("Loki 502 Bad Gateway", status_code=502))

    response, _, group = _run(client, policy.id, provider, [])

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == QueryRunStatus.FAILED.value
    assert "Loki 502 Bad Gateway" in body["error_message"]
    assert body["group_count"] == 0
    group.assert_not_called()

    run = db.get(QueryRun, body["id"])
    assert run is not None
    assert run.status == QueryRunStatus.FAILED.value
    assert run.finished_at is not None


def test_failed_grouping_records_the_run(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    provider = FakeLogSource(fetch_result=FetchResult(records=[log_record("a")]))

    with (
        patch("app.policies.integrations.build_provider", return_value=provider),
        patch(
            "app.policies.integrations.group_records", side_effect=ValueError("정규화 규칙 오류")
        ),
    ):
        response = client.post(f"/api/policies/{policy.id}/query-runs", json={})

    body = response.json()
    assert body["status"] == QueryRunStatus.FAILED.value
    assert "정규화 규칙 오류" in body["error_message"]
    assert db.query(ErrorGroup).count() == 0


def test_clamp_warnings_survive_failure(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection, default_range_minutes=60)
    provider = FakeLogSource(fetch_error=LogSourceError("timeout"))

    response, _, _ = _run(
        client,
        policy.id,
        provider,
        [],
        range_start=(NOW - timedelta(days=1)).isoformat(),
        range_end=NOW.isoformat(),
    )
    assert "range_clamped" in _warning_codes(response.json())


# ------------------------------------------------------------ 실행 가능성


def test_inactive_policy_cannot_run(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection, active=False)
    provider = FakeLogSource(fetch_result=FetchResult())

    response, _, _ = _run(client, policy.id, provider, [])
    assert response.status_code == 409


def test_inactive_connection_cannot_run(client, db) -> None:
    connection = make_connection(db, active=False)
    policy = make_policy(db, connection)
    provider = FakeLogSource(fetch_result=FetchResult())

    response, _, _ = _run(client, policy.id, provider, [])
    assert response.status_code == 409


def test_unknown_policy_returns_404(client) -> None:
    assert client.post("/api/policies/999/query-runs", json={}).status_code == 404


# ------------------------------------------------------- 실제 그룹화 연동


def test_failure_message_does_not_leak_url_credentials(client, db) -> None:
    """`query_runs.error_message` 는 DB 에 남고 화면으로 나간다 — 자격증명이 새면 안 된다.

    마스킹은 화면 표시 전과 LLM 전송 직전 두 곳에 걸리는데, **오류 경로는 그 두 곳
    어디도 지나지 않는다.** 저장 전에 한 번 더 건다.
    """
    connection = make_connection(db)
    policy = make_policy(db, connection)
    provider = FakeLogSource(
        fetch_error=LogSourceError(
            "Loki 요청에 실패했습니다: connect to http://loki-user:sup3rs3cr3t@loki:3100"
        )
    )

    response, _, _ = _run(client, policy.id, provider, [])

    body = response.json()
    assert body["status"] == QueryRunStatus.FAILED.value
    assert "sup3rs3cr3t" not in body["error_message"]
    assert "<MASKED:URI_CREDENTIALS>" in body["error_message"]
    assert "sup3rs3cr3t" not in (db.get(QueryRun, body["id"]).error_message or "")


def test_group_columns_are_clipped_to_the_column_width(client, db) -> None:
    """service/environment/error_type 은 로그가 주는 값이라 길이를 보장할 수 없다.

    컬럼 폭을 넘기면 PostgreSQL 이 조회 전체를 실패시키고, SQLite 는 조용히 통과시켜
    환경마다 결과가 갈린다. 저장 시점에 자른다.
    """
    connection = make_connection(db)
    policy = make_policy(db, connection)
    long_group = grouped_error("fp-long")
    long_group.service = "s" * 300
    long_group.environment = "e" * 200
    long_group.error_type = "E" * 400
    provider = FakeLogSource(fetch_result=FetchResult(records=[log_record("x")]))

    response, _, _ = _run(client, policy.id, provider, [long_group])

    assert response.status_code == 201, response.text
    stored = db.query(ErrorGroup).one()
    assert len(stored.service) == 128
    assert len(stored.environment) == 64
    assert len(stored.error_type) == 255


# ---------------------------------------------------------------- preview


def test_preview_limit_is_capped_by_the_schema(client, db) -> None:
    connection = make_connection(db)
    payload = {
        "loki_connection_id": connection.id,
        "logql": '{service="payment-api"}',
        "range_minutes": 60,
        "limit": 201,
        "exclusions": [],
    }
    assert client.post("/api/policies/preview", json=payload).status_code == 422


def test_preview_returns_at_most_fifty_sample_lines(client, db) -> None:
    """집계 수치는 그대로 두고 화면에 뿌리는 줄만 자른다."""
    connection = make_connection(db)
    provider = FakeLogSource(
        fetch_result=FetchResult(
            records=[log_record(f"TimeoutError {index}") for index in range(200)],
            fetched=200,
        )
    )

    with patch("app.policies.integrations.build_provider", return_value=provider):
        body = client.post(
            "/api/policies/preview",
            json={
                "loki_connection_id": connection.id,
                "logql": '{service="payment-api"}',
                "range_minutes": 60,
                "limit": 200,
                "exclusions": [],
            },
        ).json()

    assert body["fetched"] == 200
    assert len(body["sample_lines"]) == 50


def test_real_grouping_stores_only_masked_logs(client, db) -> None:
    """`group_records` 만은 mock 하지 않고 실제로 태워, 원문이 DB 에 닿지 않는지 본다.

    되돌릴 수 없는 리스크는 민감정보 유출 하나뿐이라, 정책 API 쪽 저장 경로에서도
    한 번 못을 박아 둔다. 그룹화 트랙이 아직 없으면 건너뛴다.
    """
    pytest.importorskip("app.grouping.service")

    connection = make_connection(db)
    policy = make_policy(db, connection)
    secret = "sk-live-SECRET99"
    provider = FakeLogSource(
        fetch_result=FetchResult(
            records=[
                log_record(f"TimeoutError: gateway timed out Authorization: Bearer {secret}")
            ],
            fetched=1,
        )
    )

    with patch("app.policies.integrations.build_provider", return_value=provider):
        response = client.post(f"/api/policies/{policy.id}/query-runs", json={})

    assert response.status_code == 201, response.text
    assert response.json()["group_count"] == 1

    group = db.query(ErrorGroup).one()
    assert len(group.fingerprint) <= 64
    sample = db.query(ErrorSample).one()
    assert secret not in sample.masked_log
    assert secret not in group.normalized_message
    assert sample.masking_rule_version
