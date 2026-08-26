"""일일 분석 한도의 "하루" 경계 — `app_settings.timezone` 의 로컬 자정 기준.

Phase 4 — 1 차 사용 피드백.

원래 경계는 UTC 자정이었다. 한국에서 쓰면 **업무 시작 시각인 오전 9 시**에 카운터가
리셋되므로 "어제 저녁에 쓴 분량이 오늘 아침까지 남아 있다가 9 시에 갑자기 풀린다"는
상태가 된다. 여기서 고정하는 것은 셋이다.

1. 경계는 UTC 자정이 아니라 **설정된 타임존의 로컬 자정**이다.
2. 그 타임존은 서버 로케일이 아니라 `app_settings.timezone` 이 정한다 (기본 Asia/Seoul).
3. 타임존을 UTC 로 되돌리면 옛 동작이 그대로 돌아온다 — 즉 하드코딩이 아니다.

경계 케이스는 **UTC 자정과 KST 자정 사이(00:00Z ~ 09:00Z)** 의 시각으로 잡는다.
그 구간 밖에서는 두 기준이 같은 답을 내서 회귀를 잡지 못한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.analysis import service as analysis_service
from app.models import SETTING_TIMEZONE, AppSetting
from app.policies import service as policy_service
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

#: 2026-08-26 09:30 KST. UTC 로는 아직 26 일 00:30 이라 **두 기준이 갈리는** 시각이다.
NOW_UTC = datetime(2026, 8, 26, 0, 30, tzinfo=UTC)

#: 같은 KST 날짜(26 일 08:00 KST)지만 UTC 로는 전날(25 일 23:00Z).
#: UTC 자정 기준이면 이 건이 "어제"로 빠진다 — 그게 고쳐야 했던 증상이다.
SAME_KST_DAY = datetime(2026, 8, 25, 23, 0, tzinfo=UTC)

#: 직전 KST 날짜(25 일 23:00 KST = 25 일 14:00Z). 어느 기준으로도 "어제"다.
PREVIOUS_KST_DAY = datetime(2026, 8, 25, 14, 0, tzinfo=UTC)


def _group(db, **policy_overrides):
    """연결 → 정책 → 조회 회차 → 오류 그룹 한 벌."""
    connection = make_connection(db)
    policy = make_policy(db, connection, **policy_overrides)
    run = make_query_run(db, policy)
    return policy, run, make_error_group(db, run, fingerprint="fp-timeout", count=11)


# ------------------------------------------------------- _start_of_day 단위


def test_start_of_day_is_local_midnight_expressed_in_utc() -> None:
    """KST 자정(= 전날 15:00Z)이지 UTC 자정이 아니다."""
    assert analysis_service._start_of_day(NOW_UTC, KST) == datetime(
        2026, 8, 25, 15, 0, tzinfo=UTC
    )


def test_start_of_day_with_utc_matches_the_old_behaviour() -> None:
    assert analysis_service._start_of_day(NOW_UTC, UTC) == datetime(
        2026, 8, 26, 0, 0, tzinfo=UTC
    )


def test_start_of_day_converts_to_local_first_not_after() -> None:
    """순서가 뒤집히면(UTC 자정을 구한 뒤 로컬 변환) 9 시간 어긋난다."""
    wrong_order = NOW_UTC.replace(hour=0, minute=0, second=0, microsecond=0)
    assert wrong_order - analysis_service._start_of_day(NOW_UTC, KST) == timedelta(hours=9)


def test_start_of_day_is_stable_across_the_utc_midnight_gap() -> None:
    """00:00Z ~ 09:00Z 는 전부 같은 KST 날짜다 — 경계가 도중에 움직이지 않는다."""
    boundary = datetime(2026, 8, 25, 15, 0, tzinfo=UTC)
    for hour in (0, 3, 6, 8):
        moment = datetime(2026, 8, 26, hour, 30, tzinfo=UTC)
        assert analysis_service._start_of_day(moment, KST) == boundary


# ------------------------------------------------- 타임존 설정 읽기 경로


def test_timezone_defaults_to_asia_seoul(db) -> None:
    assert policy_service.analysis_timezone_name(db) == "Asia/Seoul"
    assert policy_service.analysis_timezone(db) == KST


def test_timezone_setting_overrides_the_default(db) -> None:
    set_setting(db, SETTING_TIMEZONE, "UTC")
    assert policy_service.analysis_timezone_name(db) == "UTC"


@pytest.mark.parametrize("stored", ["Asia/Seaoul", "", 42])
def test_broken_timezone_row_falls_back_to_the_default(db, stored) -> None:
    """한도는 비용 차단 장치다 — 설정이 깨졌다고 예외로 막히거나 무제한이 되면 안 된다."""
    db.add(AppSetting(key=SETTING_TIMEZONE, value=stored))
    db.commit()

    assert policy_service.analysis_timezone_name(db) == "Asia/Seoul"


# --------------------------------------------------- daily_usage 날짜 경계


def test_job_earlier_the_same_kst_day_still_counts(db) -> None:
    """25 일 23:00Z(= 26 일 08:00 KST)는 26 일 09:30 KST 시점에서 **오늘**이다."""
    policy, run, _ = _group(db)
    other = make_error_group(db, run, fingerprint="fp-other")
    make_job_row(db, other, requested_at=SAME_KST_DAY)

    with patch("app.analysis.service._now", return_value=NOW_UTC):
        global_used, policy_used = analysis_service.daily_usage(db, policy)

    assert global_used == 1
    assert policy_used == 1


def test_job_from_the_previous_kst_day_does_not_count(db) -> None:
    policy, run, _ = _group(db)
    other = make_error_group(db, run, fingerprint="fp-other")
    make_job_row(db, other, requested_at=PREVIOUS_KST_DAY)

    with patch("app.analysis.service._now", return_value=NOW_UTC):
        global_used, policy_used = analysis_service.daily_usage(db, policy)

    assert global_used == 0
    assert policy_used == 0


def test_switching_the_setting_to_utc_restores_the_old_boundary(db) -> None:
    """타임존이 하드코딩이 아니라 **설정값**이라는 증거."""
    policy, run, _ = _group(db)
    other = make_error_group(db, run, fingerprint="fp-other")
    make_job_row(db, other, requested_at=SAME_KST_DAY)
    set_setting(db, SETTING_TIMEZONE, "UTC")

    with patch("app.analysis.service._now", return_value=NOW_UTC):
        global_used, _unused = analysis_service.daily_usage(db, policy)

    assert global_used == 0


# ------------------------------------------------------------- 429 메시지


def test_429_detail_names_the_reset_timezone(client, db) -> None:
    """언제 풀리는지가 안 보이면 사용자는 "왜 막혔는지" 를 알 수 없다."""
    _, _run, group = _group(db)
    set_daily_limit(db, 0)

    response = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={})

    assert response.status_code == 429, response.text
    detail = response.json()["detail"]
    assert "Asia/Seoul" in detail
    assert "UTC 자정" not in detail


def test_429_detail_follows_the_timezone_setting(client, db) -> None:
    _, _run, group = _group(db)
    set_daily_limit(db, 0)
    set_setting(db, SETTING_TIMEZONE, "America/New_York")

    response = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={})

    assert response.status_code == 429, response.text
    assert "America/New_York" in response.json()["detail"]


def test_policy_429_detail_names_the_reset_timezone(client, db) -> None:
    _, run, group = _group(db, daily_analysis_limit=1)
    make_llm_connection(db)
    other = make_error_group(db, run, fingerprint="fp-other")
    make_job_row(db, other, requested_at=SAME_KST_DAY)

    with patch("app.analysis.service._now", return_value=NOW_UTC):
        response = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={})

    assert response.status_code == 429, response.text
    detail = response.json()["detail"]
    assert "정책" in detail
    assert "Asia/Seoul" in detail


# --------------------------------------------------- 설정 쓰기 경로 검증


def test_timezone_setting_accepts_iana_names(client) -> None:
    response = client.put(f"/api/settings/{SETTING_TIMEZONE}", json={"value": "Europe/Berlin"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["value"] == "Europe/Berlin"
    assert body["effective_value"] == "Europe/Berlin"


@pytest.mark.parametrize("value", ["Asia/Seaoul", "KST", "", "  ", 9, 32.4, ["Asia/Seoul"]])
def test_timezone_setting_rejects_non_iana_values(client, value) -> None:
    """오타를 받아 두면 한도 판정 시점에 터진다 — 쓰기 시점에 막는다."""
    response = client.put(f"/api/settings/{SETTING_TIMEZONE}", json={"value": value})

    assert response.status_code == 422, response.text


def test_timezone_setting_null_means_server_default(client) -> None:
    client.put(f"/api/settings/{SETTING_TIMEZONE}", json={"value": "UTC"})

    response = client.put(f"/api/settings/{SETTING_TIMEZONE}", json={"value": None})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["value"] is None
    assert body["effective_value"] == "Asia/Seoul"
