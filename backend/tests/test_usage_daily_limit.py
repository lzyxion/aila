"""`GET /api/usage/daily-limit` — 오늘의 분석 한도 소진 게이지 (Phase 7).

이 게이지가 존재하는 이유는 하나다: **429 가 나기 전에** 얼마나 남았는지 보여 주는 것.
그래서 여기서 고정하는 성질도 하나로 모인다 — 게이지의 숫자와 한도 검사의 숫자가
**같은 계산에서 나온다**.

1. 사용량은 `analysis.service.daily_usage` 를 그대로 부른다 (별도 집계를 만들지 않는다).
   `app.usage.integrations` 가 그 단일 통로이자 mock 지점이다.
2. 하루 경계는 `app_settings.timezone` 의 로컬 자정이다 — 한도가 쓰는 바로 그 경계.
   경계 케이스는 **UTC 자정과 KST 자정 사이(00:00Z ~ 09:00Z)** 로 잡아야 회귀가 잡힌다.
3. `policies` 에는 자체 한도를 가진 정책만 온다 (active 무관 — 이미 쓴 이력은 사실이다).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.analysis import service as analysis_service
from app.models import SETTING_TIMEZONE
from tests.test_analysis_fixtures import (  # noqa: F401 - fixture 재수출
    client,
    db,
    engine,
    make_connection,
    make_error_group,
    make_job_row,
    make_llm_connection,
    make_policy,
    make_query_run,
    no_real_log_source,
    session_factory,
    set_daily_limit,
    set_setting,
)

KST = ZoneInfo("Asia/Seoul")

#: 2026-08-26 09:30 KST = 26 일 00:30Z. UTC 기준과 KST 기준이 **갈리는** 시각이다.
NOW_UTC = datetime(2026, 8, 26, 0, 30, tzinfo=UTC)

#: 26 일 08:00 KST (= 25 일 23:00Z). KST 기준으로는 오늘, UTC 기준으로는 어제.
SAME_KST_DAY = datetime(2026, 8, 25, 23, 0, tzinfo=UTC)

#: 25 일 23:00 KST (= 25 일 14:00Z). 어느 기준으로도 어제다.
PREVIOUS_KST_DAY = datetime(2026, 8, 25, 14, 0, tzinfo=UTC)

ENDPOINT = "/api/usage/daily-limit"


def _policy_with_group(db, *, name="payment-api ERROR", fingerprint="fp-a", **overrides):
    """연결 → 정책 → 조회 회차 → 오류 그룹 한 벌."""
    connection = make_connection(db, name=f"loki-{name}")
    policy = make_policy(db, connection, name=name, **overrides)
    run = make_query_run(db, policy)
    return policy, run, make_error_group(db, run, fingerprint=fingerprint)


def _get(client) -> dict:
    response = client.get(ENDPOINT)
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------ 응답 형태


def test_returns_the_gauge_shape_with_defaults(client, db) -> None:
    body = _get(client)

    assert set(body) == {"date", "timezone", "global_limit", "global_used", "policies"}
    assert body["timezone"] == "Asia/Seoul"
    assert body["global_used"] == 0
    assert body["policies"] == []
    # 기본 타임존의 오늘 — 서버 로케일이 아니라 설정값 기준이다.
    assert body["date"] == datetime.now(UTC).astimezone(KST).strftime("%Y-%m-%d")


def test_global_limit_comes_from_app_settings(client, db) -> None:
    set_daily_limit(db, 7)

    assert _get(client)["global_limit"] == 7


def test_date_is_a_plain_local_day_string(client, db) -> None:
    set_setting(db, SETTING_TIMEZONE, "America/New_York")

    body = _get(client)

    assert body["timezone"] == "America/New_York"
    assert body["date"] == datetime.now(UTC).astimezone(
        ZoneInfo("America/New_York")
    ).strftime("%Y-%m-%d")


# ------------------------------------------------------------- 전역 사용량


def test_global_used_counts_todays_analysis_jobs(client, db) -> None:
    _policy, run, group = _policy_with_group(db)
    make_job_row(db, group)
    make_job_row(db, make_error_group(db, run, fingerprint="fp-b"))

    assert _get(client)["global_used"] == 2


def test_global_used_ignores_jobs_from_previous_days(client, db) -> None:
    _policy, _run, group = _policy_with_group(db)
    make_job_row(db, group, requested_at=PREVIOUS_KST_DAY)

    with patch("app.analysis.service._now", return_value=NOW_UTC):
        body = _get(client)

    assert body["global_used"] == 0
    assert body["date"] == "2026-08-26"


def test_failed_jobs_still_consume_the_limit(client, db) -> None:
    """한도 검사는 상태를 가리지 않고 센다 — 게이지도 같아야 한다."""
    _policy, _run, group = _policy_with_group(db)
    make_job_row(db, group, status="failed")

    assert _get(client)["global_used"] == 1


# --------------------------------------------------------------- 정책 목록


def test_only_policies_with_their_own_limit_are_listed(client, db) -> None:
    """한도 없는 정책은 전역 게이지에 이미 들어 있다 — 실으면 별도 상한처럼 읽힌다."""
    _policy_with_group(db, name="no-limit", fingerprint="fp-no-limit")
    limited, _run, _group = _policy_with_group(
        db, name="with-limit", fingerprint="fp-limited", daily_analysis_limit=3
    )

    policies = _get(client)["policies"]

    assert [row["policy_id"] for row in policies] == [limited.id]
    assert policies[0] == {
        "policy_id": limited.id,
        "name": "with-limit",
        "limit": 3,
        "used": 0,
    }


def test_inactive_policy_with_a_limit_is_still_listed(client, db) -> None:
    """정책을 껐다고 오늘 이미 쓴 이력이 사라지지는 않는다 (전역 한도를 이미 깎았다)."""
    policy, _run, group = _policy_with_group(
        db, name="paused", fingerprint="fp-paused", active=False, daily_analysis_limit=5
    )
    make_job_row(db, group)

    policies = _get(client)["policies"]

    assert [(row["policy_id"], row["used"]) for row in policies] == [(policy.id, 1)]


def test_policy_used_counts_only_that_policys_jobs(client, db) -> None:
    first, first_run, first_group = _policy_with_group(
        db, name="alpha", fingerprint="fp-alpha", daily_analysis_limit=4
    )
    second, _second_run, second_group = _policy_with_group(
        db, name="beta", fingerprint="fp-beta", daily_analysis_limit=2
    )
    make_job_row(db, first_group)
    make_job_row(db, make_error_group(db, first_run, fingerprint="fp-alpha-2"))
    make_job_row(db, second_group)

    body = _get(client)

    assert body["global_used"] == 3
    assert [(row["policy_id"], row["limit"], row["used"]) for row in body["policies"]] == [
        (first.id, 4, 2),
        (second.id, 2, 1),
    ]


# --------------------------------------- 한도 검사와 같은 계산인가 (핵심)


def test_gauge_and_limit_check_agree_on_the_day_boundary(client, db) -> None:
    """26 일 08:00 KST 건은 26 일 09:30 KST 시점에서 **오늘**이다 (UTC 로는 어제)."""
    policy, run, _group = _policy_with_group(
        db, fingerprint="fp-boundary", daily_analysis_limit=5
    )
    make_job_row(db, make_error_group(db, run, fingerprint="fp-earlier"),
                 requested_at=SAME_KST_DAY)

    with patch("app.analysis.service._now", return_value=NOW_UTC):
        body = _get(client)
        global_used, policy_used = analysis_service.daily_usage(db, policy)

    assert body["date"] == "2026-08-26"
    # 게이지가 한도 검사와 **같은 숫자**를 보인다는 단언 그 자체.
    assert (body["global_used"], body["policies"][0]["used"]) == (global_used, policy_used)
    assert body["global_used"] == 1


def test_boundary_follows_the_timezone_setting(client, db) -> None:
    """타임존을 UTC 로 되돌리면 같은 건이 "어제"가 된다 — 경계가 하드코딩이 아니다."""
    _policy, run, _group = _policy_with_group(db, fingerprint="fp-tz")
    make_job_row(db, make_error_group(db, run, fingerprint="fp-earlier"),
                 requested_at=SAME_KST_DAY)
    set_setting(db, SETTING_TIMEZONE, "UTC")

    with patch("app.analysis.service._now", return_value=NOW_UTC):
        body = _get(client)

    assert body["timezone"] == "UTC"
    assert body["date"] == "2026-08-26"
    assert body["global_used"] == 0


def test_exhausted_policy_gauge_matches_the_429(client, db) -> None:
    """게이지가 "다 썼다"고 적힌 그 순간 실행은 429 여야 한다 (반대도 마찬가지)."""
    _policy, run, group = _policy_with_group(
        db, fingerprint="fp-exhausted", daily_analysis_limit=1
    )
    make_llm_connection(db)
    make_job_row(db, make_error_group(db, run, fingerprint="fp-used"))

    body = _get(client)
    response = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={})

    assert (body["policies"][0]["used"], body["policies"][0]["limit"]) == (1, 1)
    assert response.status_code == 429, response.text


def test_gauge_reads_usage_through_the_analysis_track(client, db) -> None:
    """사용량은 usage 트랙이 다시 세지 않는다 — `integrations` 를 통해 분석 트랙이 센다."""
    policy, _run, _group = _policy_with_group(
        db, fingerprint="fp-mocked", daily_analysis_limit=9
    )

    with patch("app.usage.integrations.daily_usage", return_value=(7, 3)) as spy:
        body = _get(client)

    assert (body["global_used"], body["policies"][0]["used"]) == (7, 3)
    # 전역 한 번 + 한도 있는 정책마다 한 번.
    assert [call.args[1] for call in spy.call_args_list][0] is None
    assert [getattr(call.args[1], "id", None) for call in spy.call_args_list][1] == policy.id
