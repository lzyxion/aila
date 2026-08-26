"""사용량 분해 (Phase 6) — `GET /api/usage?group_by=day|policy`.

기존 모델별 집계(`items`)는 그대로 두고 `buckets` 를 **추가**한다. 고정할 것:

- `day` 의 하루 경계는 서버 로케일이 아니라 `app_settings.timezone` 의 로컬 자정이다
  — 일일 분석 한도와 **같은 규칙**이어야 "오늘 3 건 썼다"가 두 화면에서 같은 뜻이 된다.
- `policy` 는 usage → job → group → run → policy 조인이고, 고리가 끊긴 기록은
  `key="unknown"` 으로 간다 (조용히 사라지면 합계가 맞지 않는다).
- 버킷의 `estimated_cost` 는 계산 가능한 기록이 하나도 없으면 **None** 이다.
  0 으로 적으면 "쌌다"로 읽힌다 (`items` 와 같은 규칙).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.models import SETTING_TIMEZONE
from tests.test_analysis_fixtures import (  # noqa: F401 - fixture 재수출
    client,
    db,
    engine,
    make_connection,
    make_error_group,
    make_job_row,
    make_policy,
    make_query_run,
    make_usage_record,
    no_real_log_source,
    session_factory,
    set_setting,
)

#: UTC 15:30 은 KST 로 **다음 날** 00:30 이다 — 날짜 경계 검증의 핵심 값.
LATE = datetime(2026, 8, 20, 15, 30, tzinfo=UTC)
EARLY = datetime(2026, 8, 20, 14, 0, tzinfo=UTC)

RANGE = {
    "range_start": (EARLY - timedelta(days=3)).isoformat(),
    "range_end": (LATE + timedelta(days=3)).isoformat(),
}


def usage(client, **params):
    response = client.get("/api/usage", params={**RANGE, **params})
    assert response.status_code == 200, response.text
    return response.json()


def make_group_under(db, *, policy_name: str, fingerprint: str):
    connection = make_connection(db, name=f"loki-{policy_name}")
    policy = make_policy(db, connection, name=policy_name)
    run = make_query_run(db, policy)
    return policy, make_error_group(db, run, fingerprint=fingerprint)


def add_usage(db, group, **overrides):
    job = make_job_row(db, group)
    return make_usage_record(db, job, **overrides)


def buckets_by_key(body) -> dict[str, dict]:
    return {bucket["key"]: bucket for bucket in body["buckets"]}


# ------------------------------------------------------------ 기존 동작 유지


def test_without_group_by_the_response_is_unchanged(client, db) -> None:
    _, group = make_group_under(db, policy_name="p1", fingerprint="fp-1")
    add_usage(db, group, created_at=EARLY)

    body = usage(client)

    # 분해를 요청하지 않았다는 것과 "분해했더니 비었다"는 다른 상태다.
    assert body["buckets"] is None
    assert body["items"][0]["model"] == "gpt-4o-mini"
    assert body["total_jobs"] == 1


def test_unknown_group_by_is_rejected(client, db) -> None:
    """오타를 조용히 무시하면 화면은 빈 표를 보여주면서 이유를 말하지 못한다."""
    response = client.get("/api/usage", params={**RANGE, "group_by": "week"})
    assert response.status_code == 422
    assert response.json()["detail"]


# --------------------------------------------------------------- group_by=day


def test_day_buckets_use_the_configured_timezone_midnight(client, db) -> None:
    set_setting(db, SETTING_TIMEZONE, "Asia/Seoul")
    _, group = make_group_under(db, policy_name="p1", fingerprint="fp-1")
    add_usage(db, group, created_at=EARLY, input_tokens=100, output_tokens=10)
    add_usage(db, group, created_at=LATE, input_tokens=200, output_tokens=20)

    body = usage(client, group_by="day")
    rows = buckets_by_key(body)

    # UTC 로 묶으면 둘 다 2026-08-20 이 된다 — KST 자정을 넘긴 쪽은 21 일이다.
    assert sorted(rows) == ["2026-08-20", "2026-08-21"]
    assert rows["2026-08-20"]["input_tokens"] == 100
    assert rows["2026-08-21"]["input_tokens"] == 200
    assert rows["2026-08-21"]["label"] == "2026-08-21"


def test_day_buckets_follow_a_timezone_change(client, db) -> None:
    set_setting(db, SETTING_TIMEZONE, "UTC")
    _, group = make_group_under(db, policy_name="p1", fingerprint="fp-1")
    add_usage(db, group, created_at=LATE)

    assert list(buckets_by_key(usage(client, group_by="day"))) == ["2026-08-20"]

    set_setting(db, SETTING_TIMEZONE, "Asia/Seoul")
    assert list(buckets_by_key(usage(client, group_by="day"))) == ["2026-08-21"]


def test_day_buckets_are_ordered_and_carry_counts(client, db) -> None:
    set_setting(db, SETTING_TIMEZONE, "UTC")
    _, group = make_group_under(db, policy_name="p1", fingerprint="fp-1")
    add_usage(db, group, created_at=EARLY - timedelta(days=1))
    add_usage(db, group, created_at=EARLY, status="failed")
    add_usage(db, group, created_at=EARLY + timedelta(minutes=5))

    rows = usage(client, group_by="day")["buckets"]

    assert [row["key"] for row in rows] == ["2026-08-19", "2026-08-20"]
    assert rows[1]["job_count"] == 2
    assert rows[1]["failure_count"] == 1


def test_bucket_cost_is_none_when_no_record_has_one(client, db) -> None:
    set_setting(db, SETTING_TIMEZONE, "UTC")
    _, group = make_group_under(db, policy_name="p1", fingerprint="fp-1")
    add_usage(db, group, created_at=EARLY, estimated_cost=None)
    add_usage(db, group, created_at=EARLY, estimated_cost=None)

    row = usage(client, group_by="day")["buckets"][0]

    # 0 으로 접으면 "이 날은 공짜였다"로 읽힌다 — 실제로는 단가표가 없었을 뿐이다.
    assert row["estimated_cost"] is None
    assert row["job_count"] == 2


def test_bucket_cost_sums_only_the_records_that_have_one(client, db) -> None:
    set_setting(db, SETTING_TIMEZONE, "UTC")
    _, group = make_group_under(db, policy_name="p1", fingerprint="fp-1")
    add_usage(db, group, created_at=EARLY, estimated_cost=Decimal("0.002000"))
    add_usage(db, group, created_at=EARLY, estimated_cost=None)

    row = usage(client, group_by="day")["buckets"][0]

    assert Decimal(row["estimated_cost"]) == Decimal("0.002000")


def test_day_buckets_respect_the_existing_filters(client, db) -> None:
    set_setting(db, SETTING_TIMEZONE, "UTC")
    _, group = make_group_under(db, policy_name="p1", fingerprint="fp-1")
    add_usage(db, group, created_at=EARLY, model="gpt-4o-mini", input_tokens=100)
    add_usage(db, group, created_at=EARLY, model="claude-3-5-haiku", input_tokens=700)

    rows = usage(client, group_by="day", model="gpt-4o-mini")["buckets"]

    assert len(rows) == 1
    assert rows[0]["input_tokens"] == 100


# ------------------------------------------------------------ group_by=policy


def test_policy_buckets_join_through_job_group_run(client, db) -> None:
    first, group_a = make_group_under(db, policy_name="payment", fingerprint="fp-a")
    second, group_b = make_group_under(db, policy_name="auth", fingerprint="fp-b")
    add_usage(db, group_a, created_at=EARLY, input_tokens=100)
    add_usage(db, group_a, created_at=EARLY, input_tokens=50, status="failed")
    add_usage(db, group_b, created_at=EARLY, input_tokens=7)

    rows = buckets_by_key(usage(client, group_by="policy"))

    assert set(rows) == {str(first.id), str(second.id)}
    assert rows[str(first.id)]["label"] == "payment"
    assert rows[str(first.id)]["input_tokens"] == 150
    assert rows[str(first.id)]["job_count"] == 2
    assert rows[str(first.id)]["failure_count"] == 1
    assert rows[str(second.id)]["label"] == "auth"


def test_usage_with_a_broken_policy_link_lands_in_unknown(client, db) -> None:
    """고리가 끊긴 기록을 빼 버리면 버킷 합과 total 이 어긋난다."""
    policy, group = make_group_under(db, policy_name="payment", fingerprint="fp-a")
    add_usage(db, group, created_at=EARLY, input_tokens=100)
    orphan_policy, orphan_group = make_group_under(db, policy_name="gone", fingerprint="fp-x")
    add_usage(db, orphan_group, created_at=EARLY, input_tokens=9)
    # 정책 행을 실제로 지운다 (조회 이력·그룹·사용 기록은 남는다).
    db.execute(text("DELETE FROM analysis_policies WHERE id = :id"), {"id": orphan_policy.id})
    db.commit()

    body = usage(client, group_by="policy")
    rows = buckets_by_key(body)

    assert set(rows) == {str(policy.id), "unknown"}
    assert rows["unknown"]["input_tokens"] == 9
    assert sum(row["job_count"] for row in body["buckets"]) == body["total_jobs"]
    # 끊긴 연결은 목록 끝에 둔다.
    assert body["buckets"][-1]["key"] == "unknown"


def test_policy_buckets_and_model_items_report_the_same_totals(client, db) -> None:
    _, group_a = make_group_under(db, policy_name="payment", fingerprint="fp-a")
    _, group_b = make_group_under(db, policy_name="auth", fingerprint="fp-b")
    add_usage(db, group_a, created_at=EARLY, input_tokens=100, output_tokens=10)
    add_usage(db, group_b, created_at=LATE, input_tokens=200, output_tokens=20)

    body = usage(client, group_by="policy")

    assert sum(row["input_tokens"] for row in body["buckets"]) == body["total_input_tokens"]
    assert sum(row["output_tokens"] for row in body["buckets"]) == body["total_output_tokens"]
    assert sum(row["job_count"] for row in body["buckets"]) == body["total_jobs"]
