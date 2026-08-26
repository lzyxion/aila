"""분석 작업 생성·실행·조회.

Phase 2 담당 트랙: **분석 플로우·usage·보고서**

여기서 지키는 계약은 넷이다 — 비용이 나가는 엔드포인트라는 것(멱등·일일 한도),
LLM 실패가 이력으로 남는다는 것, 검증이 공통 경로에서 **한 번만** 일어난다는 것,
그리고 프런트 폴링이 "영원한 running" 에 갇히지 않는다는 것(stale 전이).

BackgroundTasks 는 TestClient 가 응답 직후 **동기 실행**하므로, POST 한 번으로 실행
결과까지 검증할 수 있다. 실제 LLM API 는 호출하지 않는다 (`patched_llm`).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.enums import AnalysisJobStatus, Severity, UsageStatus
from app.models import AnalysisJob, AnalysisResult
from tests.test_analysis_fixtures import (  # noqa: F401 - fixture 재수출
    NOW,
    PRICING,
    VALID_RESULT,
    FakeLLM,
    add_samples,
    client,
    db,
    engine,
    llm_error,
    make_connection,
    make_error_group,
    make_job_row,
    make_llm_connection,
    make_policy,
    make_query_run,
    now,
    patched_llm,
    session_factory,
    set_daily_limit,
    set_pricing,
    usage_of,
)


def _group(db, **policy_overrides):
    """연결 → 정책 → 조회 회차 → 오류 그룹 한 벌."""
    connection = make_connection(db)
    policy = make_policy(db, connection, **policy_overrides)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-timeout", count=11)
    return policy, run, group


# ------------------------------------------------------------------ 생성·실행


def test_create_job_runs_analysis_and_stores_result(client, db) -> None:
    _, _, group = _group(db)
    make_llm_connection(db)
    add_samples(db, group, ["TimeoutError id=<MASKED:API_KEY> 1"])
    set_pricing(db)

    with patched_llm() as fake:
        response = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={})

    assert response.status_code == 202, response.text
    created = response.json()
    assert created["reused"] is False
    assert created["fingerprint"] == group.fingerprint
    # 응답은 백그라운드 실행 **전**에 만들어진다 — 상태는 GET 으로 폴링한다.
    assert created["status"] == AnalysisJobStatus.PENDING.value

    body = client.get(f"/api/analysis-jobs/{created['id']}").json()
    assert body["status"] == AnalysisJobStatus.SUCCEEDED.value
    assert body["error_message"] is None
    assert body["result"]["summary"] == VALID_RESULT["summary"]
    assert body["result"]["severity"] == Severity.HIGH.value
    assert body["usage"]["input_tokens"] == 1200
    assert body["usage"]["output_tokens"] == 340
    assert body["usage"]["status"] == UsageStatus.SUCCEEDED.value
    assert fake.prompts, "LLM 어댑터가 호출되지 않았습니다."

    # 목록 표시용 비정규화 컬럼도 채운다.
    db.expire_all()
    stored = db.query(AnalysisResult).filter_by(analysis_job_id=created["id"]).one()
    assert stored.summary == VALID_RESULT["summary"]
    assert stored.severity == Severity.HIGH.value
    assert stored.result_json["hypotheses"][0]["confidence"] == 0.78


def test_job_copies_provider_and_model_by_value(client, db) -> None:
    """연결 설정의 모델을 나중에 바꿔도 과거 이력은 실제 사용한 모델을 유지한다."""
    _, _, group = _group(db)
    connection = make_llm_connection(db, model="gpt-4o-mini")

    with patched_llm():
        job_id = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={}).json()["id"]

    connection.model = "gpt-4o"
    db.add(connection)
    db.commit()

    body = client.get(f"/api/analysis-jobs/{job_id}").json()
    assert (body["provider"], body["model"]) == ("openai", "gpt-4o-mini")
    assert body["usage"]["model"] == "gpt-4o-mini"


def test_create_job_uses_requested_connection(client, db) -> None:
    _, _, group = _group(db)
    make_llm_connection(db, name="default-openai", is_default=True)
    other = make_llm_connection(
        db, name="anthropic-alt", provider="anthropic", model="claude-x", is_default=False
    )

    with patched_llm():
        created = client.post(
            f"/api/error-groups/{group.id}/analysis-jobs", json={"llm_connection_id": other.id}
        ).json()

    assert created["llm_connection_id"] == other.id
    assert (created["provider"], created["model"]) == ("anthropic", "claude-x")


# --------------------------------------------------------------------- 멱등


def test_create_is_idempotent_across_query_runs_by_fingerprint(client, db) -> None:
    """어제 조회의 그룹에 진행 중인 작업이 있으면, 오늘 조회의 **다른 id** 그룹도 재사용한다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    yesterday = make_query_run(db, policy, started_at=NOW - timedelta(days=1))
    old_group = make_error_group(db, yesterday, fingerprint="fp-timeout")
    running = make_job_row(db, old_group, status=AnalysisJobStatus.RUNNING.value)

    today = make_query_run(db, policy)
    new_group = make_error_group(db, today, fingerprint="fp-timeout", count=20)
    make_llm_connection(db)

    with patched_llm() as fake:
        response = client.post(f"/api/error-groups/{new_group.id}/analysis-jobs", json={})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["reused"] is True
    assert body["id"] == running.id
    assert not fake.prompts, "재사용인데 LLM 을 호출했습니다 (중복 과금)."

    db.expire_all()
    assert db.query(AnalysisJob).count() == 1


def test_finished_job_does_not_block_a_new_one(client, db) -> None:
    _, _, group = _group(db)
    make_job_row(db, group, status=AnalysisJobStatus.SUCCEEDED.value)
    make_llm_connection(db)

    with patched_llm():
        body = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={}).json()

    assert body["reused"] is False
    db.expire_all()
    assert db.query(AnalysisJob).count() == 2


# ------------------------------------------------------------ 거부 경로 (403/422/429)


def test_policy_that_disallows_ai_analysis_is_403(client, db) -> None:
    _, _, group = _group(db, allow_ai_analysis=False)
    make_llm_connection(db)

    response = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={})

    assert response.status_code == 403, response.text
    db.expire_all()
    assert db.query(AnalysisJob).count() == 0


def test_missing_default_llm_connection_is_422(client, db) -> None:
    _, _, group = _group(db)

    response = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={})

    assert response.status_code == 422, response.text
    assert "기본 LLM 연결" in response.json()["detail"]


def test_unknown_llm_connection_is_422(client, db) -> None:
    _, _, group = _group(db)
    make_llm_connection(db)

    response = client.post(
        f"/api/error-groups/{group.id}/analysis-jobs", json={"llm_connection_id": 999}
    )

    assert response.status_code == 422, response.text


def test_inactive_llm_connection_is_422(client, db) -> None:
    _, _, group = _group(db)
    inactive = make_llm_connection(db, name="disabled", is_default=False, active=False)

    response = client.post(
        f"/api/error-groups/{group.id}/analysis-jobs", json={"llm_connection_id": inactive.id}
    )

    assert response.status_code == 422, response.text


def test_unknown_error_group_is_404(client, db) -> None:
    assert client.post("/api/error-groups/999/analysis-jobs", json={}).status_code == 404


def test_global_daily_limit_returns_429(client, db) -> None:
    _, run, group = _group(db)
    make_llm_connection(db)
    set_daily_limit(db, 1)
    # 오늘 이미 1 건을 썼다 (다른 fingerprint 라 멱등 재사용 대상이 아니다).
    other = make_error_group(db, run, fingerprint="fp-other")
    make_job_row(db, other, status=AnalysisJobStatus.SUCCEEDED.value, requested_at=now())

    with patched_llm() as fake:
        response = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={})

    assert response.status_code == 429, response.text
    assert "전역" in response.json()["detail"]
    assert not fake.prompts


def test_policy_daily_limit_returns_429(client, db) -> None:
    _, run, group = _group(db, daily_analysis_limit=1)
    make_llm_connection(db)
    other = make_error_group(db, run, fingerprint="fp-other")
    make_job_row(db, other, status=AnalysisJobStatus.SUCCEEDED.value, requested_at=now())

    response = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={})

    assert response.status_code == 429, response.text
    assert "정책" in response.json()["detail"]


def test_yesterdays_jobs_do_not_count_against_todays_limit(client, db) -> None:
    _, run, group = _group(db)
    make_llm_connection(db)
    set_daily_limit(db, 1)
    other = make_error_group(db, run, fingerprint="fp-other")
    make_job_row(db, other, requested_at=now() - timedelta(days=1))

    with patched_llm():
        response = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={})

    assert response.status_code == 202, response.text


# ------------------------------------------------------------------ 실패 경로


def test_llm_error_marks_job_failed_and_records_usage(client, db) -> None:
    _, _, group = _group(db)
    make_llm_connection(db)

    with patched_llm(FakeLLM(error=llm_error("429 rate limited"))):
        job_id = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={}).json()["id"]

    body = client.get(f"/api/analysis-jobs/{job_id}").json()
    assert body["status"] == AnalysisJobStatus.FAILED.value
    assert "429 rate limited" in body["error_message"]
    assert body["result"] is None
    assert body["usage"]["status"] == UsageStatus.FAILED.value
    assert "429 rate limited" in body["usage"]["failure_reason"]


def test_invalid_llm_response_fails_the_job_in_the_common_path(client, db) -> None:
    """검증은 어댑터 밖에서 한 번만 — 스키마가 깨지면 작업이 failed 로 남는다."""
    _, _, group = _group(db)
    make_llm_connection(db)
    broken = {"summary": "요약만 있고 가설이 없다", "severity": "high"}  # hypotheses/limitations 누락

    with patched_llm(FakeLLM(raw=broken, input_tokens=900, output_tokens=10)):
        job_id = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={}).json()["id"]

    body = client.get(f"/api/analysis-jobs/{job_id}").json()
    assert body["status"] == AnalysisJobStatus.FAILED.value
    assert "스키마" in body["error_message"]
    assert body["result"] is None
    # 검증에 실패해도 토큰은 이미 나갔다 — 사용량에 남긴다.
    assert body["usage"]["input_tokens"] == 900
    assert body["usage"]["status"] == UsageStatus.FAILED.value


# -------------------------------------------------------------- 추정 비용·단가


def test_pricing_snapshot_is_copied_from_the_settings_table(client, db) -> None:
    _, _, group = _group(db)
    make_llm_connection(db, model="gpt-4o-mini")
    set_pricing(db)

    with patched_llm(FakeLLM(input_tokens=1200, output_tokens=340)):
        job_id = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={}).json()["id"]

    usage = usage_of(db, job_id)
    # 1200/1000*0.00015 + 340/1000*0.0006
    assert usage.estimated_cost == Decimal("0.000384")
    assert usage.pricing_snapshot == {
        "model": "gpt-4o-mini",
        "input_per_1k": PRICING["gpt-4o-mini"]["input_per_1k"],
        "output_per_1k": PRICING["gpt-4o-mini"]["output_per_1k"],
        "currency": "USD",
    }


def test_unknown_model_leaves_estimated_cost_null(client, db) -> None:
    """단가표에 없으면 0 이 아니라 None 이다 (0 은 "쌌다"로 읽힌다)."""
    _, _, group = _group(db)
    make_llm_connection(db, model="gpt-does-not-exist")
    set_pricing(db)

    with patched_llm():
        job_id = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={}).json()["id"]

    usage = usage_of(db, job_id)
    assert usage.estimated_cost is None
    assert usage.pricing_snapshot is None


# --------------------------------------------------------------------- stale


def test_stale_running_job_transitions_to_failed_on_get(client, db) -> None:
    _, _, group = _group(db)
    stale = make_job_row(
        db,
        group,
        status=AnalysisJobStatus.RUNNING.value,
        requested_at=now() - timedelta(hours=1),
    )

    body = client.get(f"/api/analysis-jobs/{stale.id}").json()

    assert body["status"] == AnalysisJobStatus.FAILED.value
    assert "stale" in body["error_message"]
    # 전이는 저장된다 — 폴링이 매번 다시 계산하지 않는다.
    db.expire_all()
    assert db.get(AnalysisJob, stale.id).status == AnalysisJobStatus.FAILED.value


def test_fresh_running_job_is_left_alone(client, db) -> None:
    _, _, group = _group(db)
    fresh = make_job_row(db, group, status=AnalysisJobStatus.RUNNING.value, requested_at=now())

    body = client.get(f"/api/analysis-jobs/{fresh.id}").json()
    assert body["status"] == AnalysisJobStatus.RUNNING.value


def test_stale_job_is_not_reused_and_a_new_one_starts(client, db) -> None:
    _, _, group = _group(db)
    make_llm_connection(db)
    stale = make_job_row(
        db,
        group,
        status=AnalysisJobStatus.PENDING.value,
        requested_at=now() - timedelta(hours=1),
    )

    with patched_llm() as fake:
        body = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={}).json()

    assert body["reused"] is False
    assert body["id"] != stale.id
    assert fake.prompts
    db.expire_all()
    assert db.get(AnalysisJob, stale.id).status == AnalysisJobStatus.FAILED.value


def test_stale_transition_applies_to_the_list_endpoint(client, db) -> None:
    _, _, group = _group(db)
    make_job_row(
        db,
        group,
        status=AnalysisJobStatus.RUNNING.value,
        requested_at=now() - timedelta(hours=1),
    )

    running = client.get("/api/analysis-jobs?status=running").json()
    assert running["total"] == 0

    failed = client.get("/api/analysis-jobs?status=failed").json()
    assert failed["total"] == 1
    assert failed["items"][0]["status"] == AnalysisJobStatus.FAILED.value


# ---------------------------------------------------------------------- 목록


def test_list_orders_newest_first_and_paginates(client, db) -> None:
    _, run, group = _group(db)
    ids = [
        make_job_row(
            db,
            make_error_group(db, run, fingerprint=f"fp-{index}"),
            requested_at=now() - timedelta(minutes=index),
        ).id
        for index in range(5)
    ]

    first_page = client.get("/api/analysis-jobs?limit=2").json()
    assert first_page["total"] == 5
    assert first_page["limit"] == 2
    assert [item["id"] for item in first_page["items"]] == ids[:2]

    second_page = client.get("/api/analysis-jobs?limit=2&offset=2").json()
    assert [item["id"] for item in second_page["items"]] == ids[2:4]


def test_list_filters_by_status_and_carries_group_metadata(client, db) -> None:
    _, run, group = _group(db)
    make_llm_connection(db)
    make_job_row(db, group, status=AnalysisJobStatus.FAILED.value)

    with patched_llm():
        succeeded_id = client.post(
            f"/api/error-groups/{group.id}/analysis-jobs", json={}
        ).json()["id"]

    body = client.get("/api/analysis-jobs?status=succeeded").json()
    assert [item["id"] for item in body["items"]] == [succeeded_id]

    item = body["items"][0]
    assert item["error_group_id"] == group.id
    assert item["fingerprint"] == group.fingerprint
    assert item["service"] == "payment-api"
    assert item["error_type"] == "TimeoutError"
    assert item["severity"] == Severity.HIGH.value
    assert item["summary"] == VALID_RESULT["summary"]


def test_get_unknown_job_is_404(client, db) -> None:
    assert client.get("/api/analysis-jobs/999").status_code == 404
