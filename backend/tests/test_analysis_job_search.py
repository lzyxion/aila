"""분석 이력 검색 (Phase 6) — `GET /api/analysis-jobs` 의 `q` · 날짜 범위.

기존 필터(`status`·`limit`·`offset`)와 응답 봉투는 그대로다. 여기서 고정하는 것:

- `q` 는 **조인 쪽(서비스·정규화 메시지)과 작업 쪽(모델·fingerprint)에 OR** 로 걸린다.
  한쪽만 보면 "서비스명으로는 찾는데 모델명으로는 못 찾는" 검색이 된다.
- `total` 은 검색 결과의 건수다. 필터를 items 에만 걸고 total 을 전체로 두면
  페이지네이션이 있지도 않은 페이지를 만든다.
- 날짜는 **UTC 로 정규화해** 비교한다. 오프셋이 붙은 값을 그대로 비교하면 클라이언트
  타임존만큼 어긋난 구간을 조회하게 된다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.enums import AnalysisJobStatus
from tests.test_analysis_fixtures import (  # noqa: F401 - fixture 재수출
    client,
    db,
    engine,
    make_connection,
    make_error_group,
    make_job_row,
    make_policy,
    make_query_run,
    no_real_log_source,
    session_factory,
)

#: 검색 테스트의 기준 시각 (실제 현재 시각과 무관해야 결과가 결정적이다).
BASE = datetime(2026, 8, 20, 3, 0, 0, tzinfo=UTC)


def seed(db):
    """서로 다른 서비스·메시지·모델·fingerprint·시각을 가진 작업 세 벌."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)

    payment = make_error_group(
        db,
        run,
        fingerprint="fp-payment",
        service="payment-api",
        normalized_message="TimeoutError: payment gateway timed out",
    )
    auth = make_error_group(
        db,
        run,
        fingerprint="fp-auth",
        service="auth-service",
        normalized_message="JWTExpired: token signature expired",
    )

    jobs = {
        "payment": make_job_row(
            db, payment, model="gpt-4o-mini", requested_at=BASE - timedelta(days=2)
        ),
        "auth": make_job_row(
            db, auth, model="claude-3-5-haiku", requested_at=BASE - timedelta(hours=1)
        ),
        "auth_old": make_job_row(
            db,
            auth,
            model="gpt-4o-mini",
            status=AnalysisJobStatus.FAILED.value,
            requested_at=BASE - timedelta(days=10),
        ),
    }
    return jobs


def search(client, **params):
    response = client.get("/api/analysis-jobs", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids(body) -> set[int]:
    return {item["id"] for item in body["items"]}


# ---------------------------------------------------------------------- q


def test_search_matches_the_group_service(client, db) -> None:
    jobs = seed(db)
    body = search(client, q="payment")
    assert ids(body) == {jobs["payment"].id}
    assert body["total"] == 1


def test_search_matches_the_normalized_message(client, db) -> None:
    jobs = seed(db)
    assert ids(search(client, q="token signature")) == {jobs["auth"].id, jobs["auth_old"].id}


def test_search_matches_the_model_column(client, db) -> None:
    """모델은 `analysis_jobs` 쪽 컬럼이다 — 조인 쪽만 보면 여기서 빈 결과가 나온다."""
    jobs = seed(db)
    assert ids(search(client, q="haiku")) == {jobs["auth"].id}


def test_search_matches_the_fingerprint(client, db) -> None:
    jobs = seed(db)
    assert ids(search(client, q="fp-payment")) == {jobs["payment"].id}


def test_search_is_case_insensitive(client, db) -> None:
    jobs = seed(db)
    assert ids(search(client, q="PAYMENT-API")) == {jobs["payment"].id}


def test_search_is_a_partial_match_not_a_prefix(client, db) -> None:
    jobs = seed(db)
    assert ids(search(client, q="ateway")) == {jobs["payment"].id}


def test_blank_search_behaves_like_no_search(client, db) -> None:
    jobs = seed(db)
    assert ids(search(client, q="   ")) == {job.id for job in jobs.values()}


def test_wildcard_characters_are_literal(client, db) -> None:
    """`%` 를 이스케이프하지 않으면 검색어 하나가 전체 일치가 된다."""
    seed(db)
    assert search(client, q="%")["total"] == 0
    assert search(client, q="_")["total"] == 0


def test_search_narrows_total_not_just_items(client, db) -> None:
    seed(db)
    body = search(client, q="payment", limit=1)
    assert body["total"] == 1
    assert body["limit"] == 1
    assert len(body["items"]) == 1


def test_search_combines_with_the_status_filter(client, db) -> None:
    jobs = seed(db)
    body = search(client, q="auth-service", status=AnalysisJobStatus.FAILED.value)
    assert ids(body) == {jobs["auth_old"].id}


# ------------------------------------------------------------------ 날짜 범위


def test_requested_from_filters_older_jobs_out(client, db) -> None:
    jobs = seed(db)
    body = search(client, requested_from=(BASE - timedelta(days=3)).isoformat())
    assert ids(body) == {jobs["payment"].id, jobs["auth"].id}


def test_requested_to_filters_newer_jobs_out(client, db) -> None:
    jobs = seed(db)
    body = search(client, requested_to=(BASE - timedelta(days=1)).isoformat())
    assert ids(body) == {jobs["payment"].id, jobs["auth_old"].id}


def test_range_bounds_are_inclusive(client, db) -> None:
    jobs = seed(db)
    moment = (BASE - timedelta(hours=1)).isoformat()
    assert ids(search(client, requested_from=moment, requested_to=moment)) == {
        jobs["auth"].id
    }


def test_offset_aware_bounds_are_normalized_to_utc(client, db) -> None:
    """KST 12:00 = UTC 03:00. 오프셋을 무시하면 9 시간 어긋난 구간을 조회한다."""
    jobs = seed(db)
    kst_noon = "2026-08-20T12:00:00+09:00"  # == BASE
    assert ids(search(client, requested_to=kst_noon)) == {job.id for job in jobs.values()}
    assert ids(search(client, requested_from=kst_noon)) == set()


def test_naive_bounds_are_treated_as_utc(client, db) -> None:
    jobs = seed(db)
    naive = (BASE - timedelta(days=3)).replace(tzinfo=None).isoformat()
    body = search(client, requested_from=naive)
    assert ids(body) == {jobs["payment"].id, jobs["auth"].id}


def test_search_and_range_combine(client, db) -> None:
    jobs = seed(db)
    body = search(
        client,
        q="auth-service",
        requested_from=(BASE - timedelta(days=3)).isoformat(),
    )
    assert ids(body) == {jobs["auth"].id}


def test_plain_list_is_unchanged_without_the_new_parameters(client, db) -> None:
    """계약이 additive 라는 것 자체의 회귀 — 파라미터를 안 주면 예전 그대로다."""
    jobs = seed(db)
    body = search(client)
    assert body["total"] == 3
    assert body["limit"] == 20 and body["offset"] == 0
    # 최신순 정렬도 그대로다.
    assert [item["id"] for item in body["items"]] == [
        jobs["auth"].id,
        jobs["payment"].id,
        jobs["auth_old"].id,
    ]
