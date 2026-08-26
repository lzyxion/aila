"""Markdown 보고서 렌더링.

Phase 2 담당 트랙: **분석 플로우·usage·보고서**

보고서는 화면 밖으로 나가는 유일한 산출물이라 두 가지를 반드시 지킨다 —
**"LLM 이 생성한 원인 가설"** 표기와 원본 로그로 돌아갈 조회 조건(LogQL·라벨),
그리고 실리는 로그는 **마스킹된 값뿐**이라는 것. 저장하지 않고 요청 시점에 만든다.
"""

from __future__ import annotations

from app.enums import AnalysisJobStatus
from tests.fixtures.log_fixtures import load_secret_fixtures
from tests.test_analysis_fixtures import (  # noqa: F401 - fixture 재수출
    VALID_RESULT,
    add_samples,
    client,
    db,
    engine,
    make_connection,
    make_error_group,
    make_job_row,
    make_llm_connection,
    make_policy,
    make_query_run,
    patched_llm,
    no_real_log_source,
    session_factory,
    set_pricing,
)

LOGQL = '{service="payment-api"} | json | level="ERROR"'


def _analyzed(client, db, *, sample_lines: list[str] | None = None):
    connection = make_connection(db)
    policy = make_policy(db, connection, logql=LOGQL)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-timeout", count=11)
    make_llm_connection(db)
    set_pricing(db)
    add_samples(db, group, sample_lines or ["TimeoutError order=<MASKED:CARD> 1"])

    with patched_llm():
        job_id = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={}).json()["id"]
    return group, policy, job_id


def test_report_renders_markdown_with_the_hypothesis_label(client, db) -> None:
    group, policy, job_id = _analyzed(client, db)

    response = client.get(f"/api/analysis-jobs/{job_id}/report")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/markdown")
    body = response.text

    # 사실 확정이 아니라 가설이라는 표기.
    assert "LLM 이 생성한 원인 가설" in body
    assert "사실 확정이 아니며" in body
    # 결과 본문.
    assert VALID_RESULT["summary"] in body
    assert VALID_RESULT["hypotheses"][0]["cause"] in body
    assert VALID_RESULT["investigation_steps"][0] in body
    assert VALID_RESULT["mitigation"][0] in body
    assert VALID_RESULT["limitations"][0] in body
    # LLM 추정 심각도는 발생량 지표와 다르다는 표기를 유지한다.
    assert "발생량 기반 지표가 아닙니다" in body
    # 추정 비용은 "추정" 표기를 유지한다.
    assert "추정값이며 정산 근거가 아닙니다" in body


def test_report_carries_the_way_back_to_the_original_logs(client, db) -> None:
    group, policy, job_id = _analyzed(client, db)

    body = client.get(f"/api/analysis-jobs/{job_id}/report").text

    assert LOGQL in body
    assert policy.name in body
    assert '{environment="staging", service="payment-api"}' in body
    assert group.fingerprint in body
    assert group.first_seen.isoformat() in body


def test_report_contains_only_masked_logs(client, db) -> None:
    """원문 비밀값이 섞여 들어와도 보고서에는 남지 않는다."""
    secret = next(fixture for fixture in load_secret_fixtures() if not fixture.extra_patterns)
    _, _, job_id = _analyzed(client, db, sample_lines=[secret.raw])

    body = client.get(f"/api/analysis-jobs/{job_id}/report").text

    assert "<MASKED:" in body
    for value in secret.secrets:
        assert value not in body, "보고서에 원문 비밀값이 남았습니다."


def test_report_without_a_result_is_409(client, db) -> None:
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-running")
    job = make_job_row(db, group, status=AnalysisJobStatus.RUNNING.value)

    response = client.get(f"/api/analysis-jobs/{job.id}/report")

    assert response.status_code == 409, response.text


def test_report_for_unknown_job_is_404(client, db) -> None:
    assert client.get("/api/analysis-jobs/999/report").status_code == 404
