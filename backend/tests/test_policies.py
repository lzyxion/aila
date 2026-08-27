"""정책 CRUD 와 서버측 한도 검증.

Phase 1 담당 트랙: **정책 API**

검증 대상은 "저장되는가"가 아니라 **한도를 서버가 강제하는가**다. UI 제한은 API 를
직접 호출하면 우회되므로, 한도 초과 값이 정책으로 굳지 못하게 막는 것이 요점이다.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.models import SETTING_DAILY_ANALYSIS_LIMIT, AnalysisPolicy, AppSetting
from app.policies.service import MAX_SAMPLES_PER_GROUP_CAP
from tests.test_policies_fixtures import (  # noqa: F401 - fixture 재수출
    client,
    db,
    engine,
    make_connection,
    no_real_log_source,
    session_factory,
)


def _payload(connection_id: int, **overrides) -> dict:
    payload = {
        "log_source_connection_id": connection_id,
        "name": "payment-api ERROR",
        "description": "결제 API 오류",
        "query": '{service="payment-api"} | json | level="ERROR"',
        "default_range_minutes": 60,
        "max_lines": 1000,
        "exclusions": ["healthcheck"],
        "max_samples_per_group": 3,
        "allow_ai_analysis": True,
        "daily_analysis_limit": 10,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------- 기본 CRUD


def test_create_and_get_policy(client, db) -> None:
    connection = make_connection(db)

    response = client.post("/api/policies", json=_payload(connection.id))
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["name"] == "payment-api ERROR"
    assert created["active"] is True
    assert created["exclusions"] == ["healthcheck"]

    fetched = client.get(f"/api/policies/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


def test_list_policies_filters_by_active(client, db) -> None:
    connection = make_connection(db)
    first = client.post("/api/policies", json=_payload(connection.id, name="a")).json()
    client.post("/api/policies", json=_payload(connection.id, name="b"))
    client.delete(f"/api/policies/{first['id']}")

    assert {item["name"] for item in client.get("/api/policies").json()} == {"a", "b"}
    assert [item["name"] for item in client.get("/api/policies?active=true").json()] == ["b"]
    assert [item["name"] for item in client.get("/api/policies?active=false").json()] == ["a"]


def test_update_policy(client, db) -> None:
    connection = make_connection(db)
    created = client.post("/api/policies", json=_payload(connection.id)).json()

    response = client.patch(
        f"/api/policies/{created['id']}",
        json={"max_lines": 500, "description": None, "daily_analysis_limit": None},
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["max_lines"] == 500
    assert updated["description"] is None
    assert updated["daily_analysis_limit"] is None
    # 건드리지 않은 필드는 그대로다.
    assert updated["query"] == created["query"]


# ------------------------------------------------ baseline_query (Phase 7)


def test_baseline_query_round_trips(client, db) -> None:
    connection = make_connection(db)
    baseline = '{service="payment-api"}'

    created = client.post(
        "/api/policies", json=_payload(connection.id, baseline_query=baseline)
    ).json()

    assert created["baseline_query"] == baseline
    assert client.get(f"/api/policies/{created['id']}").json()["baseline_query"] == baseline
    assert db.get(AnalysisPolicy, created["id"]).baseline_query == baseline


def test_blank_baseline_query_is_stored_as_null(client, db) -> None:
    """빈 쿼리가 "설정됨" 으로 남으면 대시보드가 매번 분모 실패만 띄운다."""
    connection = make_connection(db)

    created = client.post(
        "/api/policies", json=_payload(connection.id, baseline_query="   ")
    ).json()

    assert created["baseline_query"] is None
    assert db.get(AnalysisPolicy, created["id"]).baseline_query is None


def test_baseline_query_can_be_set_and_cleared_by_patch(client, db) -> None:
    connection = make_connection(db)
    created = client.post("/api/policies", json=_payload(connection.id)).json()
    assert created["baseline_query"] is None

    updated = client.patch(
        f"/api/policies/{created['id']}", json={"baseline_query": '{service="x"}'}
    ).json()
    assert updated["baseline_query"] == '{service="x"}'

    # 건드리지 않은 요청은 값을 유지한다 (exclude_unset).
    kept = client.patch(f"/api/policies/{created['id']}", json={"max_lines": 10}).json()
    assert kept["baseline_query"] == '{service="x"}'

    # 명시적 null 로 지운다 — `daily_analysis_limit` 와 같은 관례다.
    cleared = client.patch(
        f"/api/policies/{created['id']}", json={"baseline_query": None}
    ).json()
    assert cleared["baseline_query"] is None

    blanked = client.patch(
        f"/api/policies/{created['id']}", json={"baseline_query": " \t "}
    ).json()
    assert blanked["baseline_query"] is None


def test_delete_deactivates_instead_of_removing(client, db) -> None:
    """정책을 지우면 query_runs·분석 이력이 맥락을 잃으므로 비활성화만 한다."""
    connection = make_connection(db)
    created = client.post("/api/policies", json=_payload(connection.id)).json()

    assert client.delete(f"/api/policies/{created['id']}").status_code == 204

    still_there = client.get(f"/api/policies/{created['id']}")
    assert still_there.status_code == 200
    assert still_there.json()["active"] is False
    assert db.get(AnalysisPolicy, created["id"]) is not None


def test_missing_policy_returns_404(client) -> None:
    assert client.get("/api/policies/999").status_code == 404
    assert client.patch("/api/policies/999", json={"max_lines": 10}).status_code == 404
    assert client.delete("/api/policies/999").status_code == 404


def test_duplicate_name_is_rejected(client, db) -> None:
    connection = make_connection(db)
    client.post("/api/policies", json=_payload(connection.id))
    duplicate = client.post("/api/policies", json=_payload(connection.id))
    assert duplicate.status_code == 409


def test_unknown_connection_is_rejected(client) -> None:
    assert client.post("/api/policies", json=_payload(4242)).status_code == 422


# --------------------------------------------------------------- 한도 검증


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("default_range_minutes", 24 * 60 + 1),
        ("max_lines", 5001),
        ("max_samples_per_group", MAX_SAMPLES_PER_GROUP_CAP + 1),
    ],
)
def test_limits_above_server_ceiling_are_rejected(client, db, field: str, value: int) -> None:
    connection = make_connection(db)
    response = client.post("/api/policies", json=_payload(connection.id, **{field: value}))
    assert response.status_code == 422, response.text
    assert field in response.json()["detail"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("default_range_minutes", 0), ("max_lines", 0), ("max_samples_per_group", 0)],
)
def test_non_positive_limits_are_rejected_by_schema(client, db, field: str, value: int) -> None:
    connection = make_connection(db)
    response = client.post("/api/policies", json=_payload(connection.id, **{field: value}))
    assert response.status_code == 422


def test_daily_analysis_limit_cannot_exceed_global_limit(client, db) -> None:
    connection = make_connection(db)
    ceiling = get_settings().default_daily_analysis_limit

    over = client.post(
        "/api/policies", json=_payload(connection.id, daily_analysis_limit=ceiling + 1)
    )
    assert over.status_code == 422
    assert "daily_analysis_limit" in over.json()["detail"]

    ok = client.post("/api/policies", json=_payload(connection.id, daily_analysis_limit=ceiling))
    assert ok.status_code == 201


def test_global_daily_limit_comes_from_app_settings(client, db) -> None:
    """전역 한도는 환경변수가 아니라 `app_settings` 행이 있으면 그 값을 쓴다."""
    connection = make_connection(db)
    db.add(AppSetting(key=SETTING_DAILY_ANALYSIS_LIMIT, value=2))
    db.commit()

    assert client.post(
        "/api/policies", json=_payload(connection.id, name="over", daily_analysis_limit=3)
    ).status_code == 422
    assert client.post(
        "/api/policies", json=_payload(connection.id, name="ok", daily_analysis_limit=2)
    ).status_code == 201


def test_invalid_exclusion_regex_is_rejected(client, db) -> None:
    """조회 시점이 아니라 저장 시점에 막는다 — 안 그러면 실행만 계속 실패한다."""
    connection = make_connection(db)
    response = client.post("/api/policies", json=_payload(connection.id, exclusions=["("]))
    assert response.status_code == 422
    assert "정규식" in response.json()["detail"]


def test_update_cannot_raise_limit_above_ceiling(client, db) -> None:
    connection = make_connection(db)
    created = client.post("/api/policies", json=_payload(connection.id)).json()

    response = client.patch(f"/api/policies/{created['id']}", json={"max_lines": 999_999})
    assert response.status_code == 422
    assert db.get(AnalysisPolicy, created["id"]).max_lines == 1000
