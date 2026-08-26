"""`/api/usage` — 모델·기간별 토큰·추정 비용 집계.

Phase 2 담당 트랙: **분석 플로우·usage·보고서**

사용량 대시보드는 **사후 확인**일 뿐이다 (비용 차단은 일일 분석 한도가 한다). 그래서
여기서 검증하는 것은 집계의 정확성과, 단가표에 없어 `estimated_cost` 가 없는 기록을
0 으로 접지 않는다는 것 두 가지다.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.enums import UsageStatus
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
    make_usage_record,
    now,
    patched_llm,
    no_real_log_source,
    session_factory,
    set_pricing,
)


def _run(db):
    connection = make_connection(db)
    policy = make_policy(db, connection)
    return make_query_run(db, policy)


def _job(db, run, fingerprint: str):
    return make_job_row(db, make_error_group(db, run, fingerprint=fingerprint))


def test_usage_aggregates_by_model(client, db) -> None:
    run = _run(db)
    make_usage_record(
        db,
        _job(db, run, "fp-1"),
        model="gpt-4o-mini",
        input_tokens=1000,
        output_tokens=200,
        estimated_cost=Decimal("0.001000"),
        latency_ms=1000,
    )
    make_usage_record(
        db,
        _job(db, run, "fp-2"),
        model="gpt-4o-mini",
        input_tokens=500,
        output_tokens=100,
        estimated_cost=Decimal("0.000500"),
        latency_ms=2000,
    )
    make_usage_record(
        db,
        _job(db, run, "fp-3"),
        provider="anthropic",
        model="claude-x",
        input_tokens=300,
        output_tokens=50,
        estimated_cost=Decimal("0.002000"),
        latency_ms=3000,
        status=UsageStatus.FAILED.value,
    )

    body = client.get("/api/usage").json()

    assert body["total_jobs"] == 3
    assert body["total_input_tokens"] == 1800
    assert body["total_output_tokens"] == 350
    assert Decimal(body["total_estimated_cost"]) == Decimal("0.003500")

    by_model = {item["model"]: item for item in body["items"]}
    openai = by_model["gpt-4o-mini"]
    assert openai["provider"] == "openai"
    assert openai["job_count"] == 2
    assert openai["failure_count"] == 0
    assert openai["input_tokens"] == 1500
    assert openai["output_tokens"] == 300
    assert Decimal(openai["estimated_cost"]) == Decimal("0.001500")
    assert openai["avg_latency_ms"] == 1500.0

    anthropic = by_model["claude-x"]
    assert anthropic["job_count"] == 1
    assert anthropic["failure_count"] == 1
    assert anthropic["avg_latency_ms"] == 3000.0


def test_usage_respects_the_requested_range(client, db) -> None:
    run = _run(db)
    make_usage_record(db, _job(db, run, "fp-old"), created_at=now() - timedelta(days=40))
    make_usage_record(db, _job(db, run, "fp-new"), created_at=now())

    # 기본 범위(최근 30 일)에는 오래된 기록이 빠진다.
    assert client.get("/api/usage").json()["total_jobs"] == 1

    # `+00:00` 은 쿼리스트링에서 인코딩되어야 한다 (params 로 넘긴다).
    start = (now() - timedelta(days=60)).isoformat()
    widened = client.get("/api/usage", params={"range_start": start}).json()
    assert widened["total_jobs"] == 2


def test_usage_filters_by_model_and_provider(client, db) -> None:
    run = _run(db)
    make_usage_record(db, _job(db, run, "fp-1"), model="gpt-4o-mini")
    make_usage_record(db, _job(db, run, "fp-2"), provider="anthropic", model="claude-x")

    assert client.get("/api/usage?model=claude-x").json()["total_jobs"] == 1
    assert client.get("/api/usage?provider=openai").json()["items"][0]["model"] == "gpt-4o-mini"


def test_usage_does_not_fold_missing_prices_into_zero(client, db) -> None:
    """단가표에 없는 모델은 비용이 None 이다 — 합계에 0 으로 섞이지 않는다."""
    run = _run(db)
    make_usage_record(db, _job(db, run, "fp-1"), estimated_cost=None, latency_ms=None)
    make_usage_record(db, _job(db, run, "fp-2"), estimated_cost=Decimal("0.002000"))

    body = client.get("/api/usage").json()
    item = body["items"][0]

    assert item["job_count"] == 2
    assert Decimal(item["estimated_cost"]) == Decimal("0.002000")
    # latency 가 없는 기록은 평균에서 제외한다.
    assert item["avg_latency_ms"] == 1200.0


def test_usage_is_empty_without_records(client, db) -> None:
    body = client.get("/api/usage").json()
    assert body["items"] == []
    assert body["total_jobs"] == 0
    # 기록이 없으면 합계는 0 이 아니라 None 이다 — 0 은 "이번 달은 공짜였다"로 읽힌다.
    assert body["total_estimated_cost"] is None


def test_totals_are_none_when_no_item_has_a_price(client, db) -> None:
    """단가표에 모델이 하나도 없으면 항목도 합계도 None 이다 (0 으로 표시 금지)."""
    run = _run(db)
    make_usage_record(db, _job(db, run, "fp-1"), estimated_cost=None)
    make_usage_record(db, _job(db, run, "fp-2"), estimated_cost=None)

    body = client.get("/api/usage").json()

    assert body["total_jobs"] == 2
    assert body["items"][0]["estimated_cost"] is None
    assert body["total_estimated_cost"] is None


def test_usage_reflects_a_real_analysis_run(client, db) -> None:
    """분석 실행 → usage 기록 → 집계까지 한 경로로 이어진다."""
    run = _run(db)
    group = make_error_group(db, run, fingerprint="fp-timeout")
    make_llm_connection(db, model="gpt-4o-mini")
    set_pricing(db)

    with patched_llm():
        client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={})

    body = client.get("/api/usage").json()
    assert body["total_jobs"] == 1
    assert body["total_input_tokens"] == 1200
    assert Decimal(body["total_estimated_cost"]) == Decimal("0.000384")
    assert body["items"][0]["failure_count"] == 0
