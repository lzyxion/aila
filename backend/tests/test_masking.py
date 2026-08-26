"""마스킹 테스트.

되돌릴 수 없는 유일한 실패가 이 모듈에서 나온다. 그래서 여기서는 "규칙이 동작한다"만
보지 않고, **비밀값 fixture 의 원문이 결과 어디에도 남지 않는다**를 직접 단언한다.
"""

from __future__ import annotations

import json

import pytest

from app.masking.rules import KINDS, PLACEHOLDER_RE
from app.masking.service import (
    MASKING_RULE_VERSION,
    MaskingPatternError,
    contains_placeholder,
    mask,
    mask_mapping,
)
from tests.fixtures.log_fixtures import all_extra_patterns, load_secret_fixtures

SECRET_FIXTURES = load_secret_fixtures()


def kinds_in(text: str) -> set[str]:
    return {match[len("<MASKED:") : -1] for match in PLACEHOLDER_RE.findall(text)}


# ------------------------------------------------------------------- 규칙 버전


def test_masking_rule_version_is_v1() -> None:
    """`error_samples.masking_rule_version` 기본값과 맞아야 한다."""
    assert MASKING_RULE_VERSION == "v1"


# --------------------------------------------------------------- 최소 규칙 전부


@pytest.mark.parametrize(
    ("raw", "secret", "kind"),
    [
        # API 키
        ("api_key=sk-live-AbCdEf0123456789XyZ", "sk-live-AbCdEf0123456789XyZ", "API_KEY"),
        ('"x-api-key": "abcdef0123456789"', "abcdef0123456789", "API_KEY"),
        ("aws key AKIAIOSFODNN7EXAMPLE rotated", "AKIAIOSFODNN7EXAMPLE", "API_KEY"),
        ("token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123", "API_KEY"),
        # Bearer / JWT
        (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln",
            "eyJhbGciOiJIUzI1NiJ9",
            "BEARER_TOKEN",
        ),
        (
            "verified eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl ok",
            "eyJhbGciOiJIUzI1NiJ9",
            "JWT",
        ),
        # 비밀번호
        ("password=hunter2", "hunter2", "PASSWORD"),
        ('{"passwd": "s3cr3t!"}', "s3cr3t!", "PASSWORD"),
        # 쿠키
        ("Cookie: sid=abc123def456; theme=dark", "abc123def456", "COOKIE"),
        ("JSESSIONID=9F8E7D6C5B4A3210", "9F8E7D6C5B4A3210", "COOKIE"),
        # DB 연결 문자열
        (
            "dsn=postgresql://aila:S3cr3tP4ss@db.internal:5432/aila",
            "S3cr3tP4ss",
            "DB_URI",
        ),
        ("mongodb+srv://u:p@cluster0.example.net/prod", "cluster0.example.net", "DB_URI"),
        ("https://svc:topsecret@api.example.com/v1", "topsecret", "URI_CREDENTIALS"),
        # 개인 식별 정보
        ("mail to admin@example.com failed", "admin@example.com", "EMAIL"),
        ("call 010-1234-5678 for support", "010-1234-5678", "PHONE"),
        ("tel +821012345678 unreachable", "+821012345678", "PHONE"),
        ("card 4111 1111 1111 1111 declined", "4111 1111 1111 1111", "CARD"),
        ("card 4111111111111111 declined", "4111111111111111", "CARD"),
    ],
)
def test_minimum_rules_remove_the_secret(raw: str, secret: str, kind: str) -> None:
    masked = mask(raw)
    assert secret not in masked, masked
    assert kind in kinds_in(masked), masked


def test_every_placeholder_kind_is_declared() -> None:
    """`<MASKED:종류>` 의 종류는 선언된 목록 안에 있어야 한다 (오타 방지)."""
    for fixture in SECRET_FIXTURES:
        masked = mask(fixture.raw, fixture.extra_patterns)
        assert kinds_in(masked) <= set(KINDS), masked


def test_custom_patterns_are_applied() -> None:
    masked = mask("employee EMP-77-1234 blocked", extra_patterns=[r"EMP-\d{2}-\d{4}"])
    assert masked == "employee <MASKED:CUSTOM> blocked"


def test_invalid_custom_pattern_fails_loudly() -> None:
    """규칙이 조용히 빠지는 것이 이 모듈에서 가장 위험한 실패다."""
    with pytest.raises(MaskingPatternError):
        mask("anything", extra_patterns=["("])


def test_mask_is_idempotent() -> None:
    """화면 표시 전과 LLM 전송 직전에 두 번 걸리므로 두 번 걸어도 같아야 한다."""
    for fixture in SECRET_FIXTURES:
        once = mask(fixture.raw, fixture.extra_patterns)
        twice = mask(once, fixture.extra_patterns)
        assert once == twice, fixture.name


def test_ordinary_error_message_survives_untouched() -> None:
    """과잉 마스킹은 그룹화를 망가뜨린다 — 평범한 오류 메시지는 그대로 남아야 한다."""
    message = "TimeoutError: payment gateway timed out after 3000ms (attempt 2 of 3)"
    assert mask(message) == message
    assert not contains_placeholder(message)


def test_empty_input() -> None:
    assert mask("") == ""


def test_mask_mapping_masks_values_not_keys() -> None:
    masked = mask_mapping({"user_email": "admin@example.com", "service": "payment-api"})
    assert masked == {"user_email": "<MASKED:EMAIL>", "service": "payment-api"}


# ------------------------------------------------- 비밀값 fixture 자동 검증


@pytest.mark.parametrize("fixture", SECRET_FIXTURES, ids=lambda f: f.name)
def test_secret_fixture_leaves_no_original_token(fixture) -> None:
    masked = mask(fixture.raw, fixture.extra_patterns)
    for secret in fixture.secrets:
        assert secret not in masked, f"{fixture.name}: {secret!r} 가 남았다 -> {masked}"
    assert set(fixture.expected_kinds) <= kinds_in(masked), masked


@pytest.mark.parametrize(
    "fixture",
    [item for item in SECRET_FIXTURES if item.format == "json"],
    ids=lambda f: f.name,
)
def test_json_logs_stay_parseable_after_masking(fixture) -> None:
    """마스킹이 JSON 구조를 깨면 뒤따르는 파싱이 통째로 비정형으로 떨어진다."""
    assert isinstance(json.loads(mask(fixture.raw, fixture.extra_patterns)), dict)


def test_llm_payload_carries_no_original_token() -> None:
    """설계 문서가 요구한 자동 테스트 — LLM 요청 페이로드에 원문 토큰이 없음을 단언한다.

    프롬프트 조립은 LLM 트랙(3 주차) 몫이지만, 그 입력이 되는 대표 로그는 여기서
    만들어진다. 그룹화 결과를 프롬프트 모양으로 직렬화해 원문이 없는지 본다.
    """
    from app.grouping.service import group_records
    from tests.fixtures.log_fixtures import record

    patterns = all_extra_patterns()
    records = [record(fixture.raw, minutes=index) for index, fixture in enumerate(SECRET_FIXTURES)]
    groups = group_records(records, max_samples_per_group=3, extra_mask_patterns=patterns)

    payload = json.dumps([group.model_dump(mode="json") for group in groups], ensure_ascii=False)
    for fixture in SECRET_FIXTURES:
        for secret in fixture.secrets:
            assert secret not in payload, f"{fixture.name}: {secret!r} 가 페이로드에 남았다"
