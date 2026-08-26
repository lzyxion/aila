"""프롬프트 조립과 **LLM 전송 직전 이중 마스킹**.

Phase 2 담당 트랙: **분석 플로우·usage·보고서**

되돌릴 수 없는 사고는 민감정보 유출 하나뿐이다. 프롬프트로 나간 토큰은 회수할 수 없으므로,
여기서는 두 가지를 단언한다.

1. 프롬프트에 들어가는 것은 설계 문서의 **고정 목록**뿐이고 대표 로그는 **최대 3 개**다.
2. `tests/fixtures/secret_logs.jsonl` 의 **원문 비밀값이 프롬프트 어디에도 없다** —
   샘플이 어떤 이유로 마스킹되지 않은 채 DB 에 들어와 있어도, 전송 직전 마스킹이 덮는다.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from app.analysis.prompt import (
    MAX_SAMPLE_CHARS,
    PromptContext,
    PromptSample,
    build_prompt,
    render_user_message,
)
from app.config import get_settings
from tests.fixtures.log_fixtures import SecretFixture, load_secret_fixtures
from tests.test_analysis_fixtures import (  # noqa: F401 - fixture 재수출
    NOW,
    add_samples,
    client,
    db,
    engine,
    make_connection,
    make_error_group,
    make_llm_connection,
    make_policy,
    make_query_run,
    patched_llm,
    session_factory,
)

#: 프롬프트에 실린 대표 로그 항목("1. [시각] 본문") 을 세는 패턴.
SAMPLE_LINE_RE = re.compile(r"^\d+\. \[", re.MULTILINE)

#: 사용자 정의 규칙이 필요한 fixture 는 정책에 규칙이 등록돼야 지워진다 —
#: 프롬프트 경로는 내장 규칙만 쓰므로 여기서는 제외한다.
BUILTIN_ONLY_SECRETS = [
    fixture for fixture in load_secret_fixtures() if not fixture.extra_patterns
]


def _context(**overrides) -> PromptContext:
    values = {
        "service": "payment-api",
        "environment": "staging",
        "error_type": "TimeoutError",
        "normalized_message": "TimeoutError: payment gateway timed out",
        "count": 11,
        "first_seen": NOW - timedelta(minutes=30),
        "last_seen": NOW,
        "labels": {"service": "payment-api", "environment": "staging"},
        "top_stack_frame": "payments/gateway.py:88",
        "samples": [PromptSample(occurred_at=NOW, masked_log="TimeoutError <MASKED:EMAIL>")],
        "trend": (),
    }
    values.update(overrides)
    return PromptContext(**values)


# ------------------------------------------------------------------ 고정 목록


def test_prompt_contains_only_the_fixed_list() -> None:
    user = render_user_message(_context())

    for expected in (
        "payment-api",
        "staging",
        (NOW - timedelta(minutes=30)).isoformat(),
        NOW.isoformat(),
        "발생 횟수: 11",
        "TimeoutError: payment gateway timed out",
        "payments/gateway.py:88",
    ):
        assert expected in user


def test_prompt_includes_trend_only_when_present() -> None:
    without = render_user_message(_context())
    assert "최근 추이" not in without

    with_trend = render_user_message(_context(trend=[(NOW, 12.0)]))
    assert "최근 추이" in with_trend
    assert "12" in with_trend


def test_prompt_caps_samples_at_three() -> None:
    samples = [
        PromptSample(occurred_at=NOW - timedelta(minutes=index), masked_log=f"MARKER-{index}")
        for index in range(6)
    ]

    user = render_user_message(_context(samples=samples))

    assert len(SAMPLE_LINE_RE.findall(user)) == get_settings().prompt_max_samples == 3
    assert "MARKER-3" not in user


def test_prompt_truncates_a_very_long_sample() -> None:
    long_sample = PromptSample(occurred_at=NOW, masked_log="x" * 9000)
    user = render_user_message(_context(samples=[long_sample]))
    assert "…(생략)" in user
    assert len(user) < MAX_SAMPLE_CHARS + 2000


def test_build_prompt_carries_schema_and_version() -> None:
    prompt = build_prompt(_context(), prompt_version="v9")
    assert prompt.prompt_version == "v9"
    required = set(prompt.json_schema["required"])
    assert required >= {"summary", "severity", "hypotheses", "limitations"}
    assert prompt.system


# --------------------------------------------------------------- 이중 마스킹


@pytest.mark.parametrize(
    "fixture", BUILTIN_ONLY_SECRETS, ids=[fixture.name for fixture in BUILTIN_ONLY_SECRETS]
)
def test_raw_secrets_never_reach_the_prompt(fixture: SecretFixture) -> None:
    """샘플이 마스킹되지 않은 채 들어와도 전송 직전 마스킹이 덮는다."""
    prompt = build_prompt(
        _context(
            normalized_message=fixture.raw,
            samples=[PromptSample(occurred_at=NOW, masked_log=fixture.raw)],
        )
    )
    text = f"{prompt.system}\n{prompt.user}"

    for secret in fixture.secrets:
        assert secret not in text, f"{fixture.name}: 원문 비밀값이 프롬프트에 남았습니다."
    assert "<MASKED:" in text


def test_double_masking_is_idempotent_for_already_masked_samples() -> None:
    already = "TimeoutError token=<MASKED:API_KEY> user=<MASKED:EMAIL>"
    prompt = build_prompt(_context(samples=[PromptSample(occurred_at=NOW, masked_log=already)]))
    assert already in prompt.user


def test_end_to_end_prompt_has_at_most_three_masked_samples(client, db) -> None:
    """실행 경로 전체 — DB 에 원문이 섞여 있어도 프롬프트에는 마스킹된 3 개만 나간다."""
    connection = make_connection(db)
    policy = make_policy(db, connection)
    run = make_query_run(db, policy)
    group = make_error_group(db, run, fingerprint="fp-secret")
    make_llm_connection(db)

    secrets = [fixture.raw for fixture in BUILTIN_ONLY_SECRETS][:2]
    add_samples(db, group, ["MARKER-old-1", "MARKER-old-2", "MARKER-old-3", *secrets])

    with patched_llm() as fake:
        response = client.post(f"/api/error-groups/{group.id}/analysis-jobs", json={})
    assert response.status_code == 202, response.text

    text = fake.prompt_text
    assert len(SAMPLE_LINE_RE.findall(fake.prompt.user)) == 3
    # 최신 3 개만 (오래된 MARKER-old-1 은 잘린다).
    assert "MARKER-old-1" not in text
    for fixture in BUILTIN_ONLY_SECRETS[:2]:
        for secret in fixture.secrets:
            assert secret not in text, f"{fixture.name}: 원문 비밀값이 프롬프트에 남았습니다."
