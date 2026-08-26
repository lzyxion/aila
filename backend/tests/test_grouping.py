"""그룹화 테스트 — 정규화 레코드(`LogRecord`) fixture 기준.

설계 문서가 못 박은 세 가지를 중심에 둔다.

- 파싱은 JSON / logfmt / 비정형을 구분한다.
- 가변값(요청 ID·숫자·시각·IP)이 달라도 같은 오류는 한 그룹이다.
- fingerprint 에는 **상위 스택 프레임만** 들어간다 (호출 경로가 달라도 한 그룹,
  상위 프레임이 다르면 다른 그룹).
"""

from __future__ import annotations

import json

import pytest

from app.grouping.fingerprint import compute_fingerprint
from app.grouping.normalize import normalize
from app.grouping.parsers import (
    FORMAT_JSON,
    FORMAT_LOGFMT,
    FORMAT_PLAIN,
    parse_line,
    top_stack_frame,
)
from app.grouping.service import (
    NORMALIZATION_RULE_VERSION,
    GroupedError,
    GroupedSample,
    group_records,
)
from app.masking.service import MASKING_RULE_VERSION
from tests.fixtures.log_fixtures import (
    JAVA_STACKTRACE,
    JSON_LINE,
    LOGFMT_LINE,
    PLAIN_LINE,
    PYTHON_TRACEBACK,
    PYTHON_TRACEBACK_OTHER_CALLER,
    PYTHON_TRACEBACK_OTHER_TOP_FRAME,
    all_extra_patterns,
    at,
    load_secret_fixtures,
    record,
)

SECRET_FIXTURES = load_secret_fixtures()


# --------------------------------------------------------------- 규칙 버전


def test_normalization_rule_version_is_v2() -> None:
    """규칙을 고치면 반드시 올린다 (`error_groups.normalization_rule_version`)."""
    assert NORMALIZATION_RULE_VERSION == "v2"


# ------------------------------------------------------------------- 파싱


def test_parses_json_log() -> None:
    parsed = parse_line(JSON_LINE)
    assert parsed.format == FORMAT_JSON
    assert parsed.message == "Timeout while calling gateway for order 91823"
    assert parsed.error_type == "java.net.SocketTimeoutException"
    assert parsed.top_stack_frame == (
        "at com.example.pay.GatewayClient.charge(GatewayClient.java:88)"
    )
    # 스택 전체는 샘플용으로 보존한다 (fingerprint 에는 상위 한 줄만 쓴다).
    assert "PaymentService.pay" in parsed.stacktrace


def test_parses_logfmt_log() -> None:
    parsed = parse_line(LOGFMT_LINE)
    assert parsed.format == FORMAT_LOGFMT
    assert parsed.message == "order 91823 failed after 1200ms"
    assert parsed.error_type == "TimeoutError"
    assert parsed.fields["upstream"] == "10.0.3.17:8443"


def test_parses_plain_log_and_strips_timestamp_and_level() -> None:
    parsed = parse_line(PLAIN_LINE)
    assert parsed.format == FORMAT_PLAIN
    assert parsed.message.startswith("TimeoutError:")
    assert parsed.error_type == "TimeoutError"
    assert parsed.stacktrace is None


def test_parses_python_traceback_message_from_the_last_line() -> None:
    parsed = parse_line(PYTHON_TRACEBACK)
    assert parsed.message == "TimeoutError: payment gateway timed out after 3000ms"
    assert parsed.error_type == "TimeoutError"
    # Python 은 `most recent call last` — 오류 지점은 **마지막** File 줄이다.
    assert parsed.top_stack_frame == 'File "/app/vendor/httpclient.py", line 118, in post'


def test_python_frame_order_is_reversed_relative_to_java() -> None:
    """언어마다 프레임 순서가 반대다 — 이 분기를 잃으면 오류 지점이 진입점으로 뒤바뀐다."""
    python_stack = (
        "Traceback (most recent call last):\n"
        '  File "/app/web/handler.py", line 10, in handle\n'
        '  File "/app/db/pool.py", line 77, in acquire\n'
        "OperationalError: pool exhausted"
    )
    assert top_stack_frame(python_stack) == 'File "/app/db/pool.py", line 77, in acquire'

    java_stack = (
        "java.lang.IllegalStateException: boom\n"
        "\tat com.example.Inner.fail(Inner.java:9)\n"
        "\tat com.example.Outer.run(Outer.java:44)"
    )
    # Java/Node 는 가장 안쪽 프레임이 먼저 나온다 -> 첫 줄이 오류 지점이다.
    assert top_stack_frame(java_stack) == "at com.example.Inner.fail(Inner.java:9)"


def test_python_traceback_without_header_still_uses_the_last_frame() -> None:
    stack = (
        '  File "/app/a.py", line 1, in a\n'
        '  File "/app/b.py", line 2, in b\n'
        "ValueError: nope"
    )
    assert top_stack_frame(stack) == 'File "/app/b.py", line 2, in b'


def test_parses_java_stacktrace() -> None:
    parsed = parse_line(JAVA_STACKTRACE)
    assert parsed.error_type == "java.lang.NullPointerException"
    assert parsed.top_stack_frame == "at com.example.pay.OrderMapper.map(OrderMapper.java:31)"


def test_unparseable_input_never_raises() -> None:
    for raw in ("", "   ", "{not json", "=", "\n\n", "}{"):
        assert parse_line(raw).format in {FORMAT_JSON, FORMAT_LOGFMT, FORMAT_PLAIN}


def test_three_formats_of_the_same_run_are_all_grouped() -> None:
    groups = group_records(
        [record(JSON_LINE), record(LOGFMT_LINE, minutes=1), record(PLAIN_LINE, minutes=2)],
        max_samples_per_group=3,
    )
    assert len(groups) == 3
    assert sum(group.count for group in groups) == 3


# ------------------------------------------------------------------ 정규화


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("order 91823 failed after 1200ms", "order <NUM> failed after <NUM>ms"),
        ("request_id=8f2a1c3e-6d55-4f10-9a2b-0c1d2e3f4a5b", "request_id=<ID>"),
        ("session 8f2a1c3e-6d55-4f10-9a2b-0c1d2e3f4a5b", "session <UUID>"),
        ("upstream 10.0.3.17:8443 refused", "upstream <IP> refused"),
        ("at 2026-08-26T01:02:03Z", "at <TS>"),
        ("obj 0xdeadbeef", "obj <HEX>"),
        ("<MASKED:EMAIL> not found", "<MASKED:EMAIL> not found"),
    ],
)
def test_normalize_replaces_variable_values(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


# ---------------------------------------------------------------- 그룹 병합


def test_variable_values_collapse_into_one_group() -> None:
    """요청 ID·소요 시간·IP 만 다른 같은 오류는 한 그룹이어야 한다."""
    records = [
        record(
            "ERROR TimeoutError: gateway timed out after 3000ms "
            "(request_id=8f2a1c3e-6d55-4f10-9a2b-0c1d2e3f4a5b, upstream=10.0.3.17)",
            minutes=0,
        ),
        record(
            "ERROR TimeoutError: gateway timed out after 4120ms "
            "(request_id=1b7c9d0e-2f34-4a56-8b90-c1d2e3f4a5b6, upstream=10.0.4.99)",
            minutes=5,
        ),
        record(
            "ERROR TimeoutError: gateway timed out after 812ms "
            "(request_id=deadbeef-0000-4a56-8b90-c1d2e3f4a5b6, upstream=10.0.9.1)",
            minutes=9,
        ),
    ]
    groups = group_records(records, max_samples_per_group=3)

    assert len(groups) == 1
    group = groups[0]
    assert group.count == 3
    assert group.error_type == "TimeoutError"
    assert group.normalized_message == (
        "TimeoutError: gateway timed out after <NUM>ms (request_id=<ID>, upstream=<IP>)"
    )
    assert group.first_seen == at(0)
    assert group.last_seen == at(9)


def test_different_exception_types_stay_separate() -> None:
    """메시지가 같아도 예외 타입이 다르면 원인이 다르다 — 합치면 안 된다."""
    records = [
        record(
            '{"message":"query failed","error_type":"psycopg.OperationalError"}', minutes=0
        ),
        record(
            '{"message":"query failed","error_type":"psycopg.ProgrammingError"}', minutes=1
        ),
    ]
    groups = group_records(records, max_samples_per_group=3)

    assert len(groups) == 2
    assert {group.error_type for group in groups} == {
        "psycopg.OperationalError",
        "psycopg.ProgrammingError",
    }
    assert {found.normalized_message for found in groups} == {"query failed"}


def test_different_services_stay_separate() -> None:
    records = [
        record("TimeoutError: gateway timed out", service="payment-api"),
        record("TimeoutError: gateway timed out", service="order-api", minutes=1),
    ]
    groups = group_records(records, max_samples_per_group=3)
    assert {group.service for group in groups} == {"payment-api", "order-api"}


# -------------------------------------------------- 상위 스택 프레임만 사용


def test_same_top_frame_different_callers_is_one_group() -> None:
    """스택 전체를 해시하면 같은 버그가 호출 경로 차이로 쪼개진다."""
    groups = group_records(
        [record(PYTHON_TRACEBACK), record(PYTHON_TRACEBACK_OTHER_CALLER, minutes=1)],
        max_samples_per_group=3,
    )
    assert len(groups) == 1
    assert groups[0].count == 2
    assert groups[0].top_stack_frame == 'File "/app/vendor/httpclient.py", line <NUM>, in post'


def test_different_top_frame_is_a_different_group() -> None:
    """호출 경로 첫 줄이 같아도 오류 지점이 다르면 다른 그룹이다.

    두 fixture 는 첫 프레임(`payment/service.py:42`)이 **같다** — v1 처럼 첫 줄을 쓰면
    이 둘이 한 그룹으로 뭉친다.
    """
    groups = group_records(
        [record(PYTHON_TRACEBACK), record(PYTHON_TRACEBACK_OTHER_TOP_FRAME, minutes=1)],
        max_samples_per_group=3,
    )
    assert len(groups) == 2


def test_fingerprint_uses_only_the_top_frame() -> None:
    parsed_a = parse_line(PYTHON_TRACEBACK)
    parsed_b = parse_line(PYTHON_TRACEBACK_OTHER_CALLER)
    assert parsed_a.stacktrace != parsed_b.stacktrace  # 스택 전체는 다르다
    assert compute_fingerprint(
        "payment-api", parsed_a.error_type, normalize(parsed_a.message),
        normalize(parsed_a.top_stack_frame),
    ) == compute_fingerprint(
        "payment-api", parsed_b.error_type, normalize(parsed_b.message),
        normalize(parsed_b.top_stack_frame),
    )


def test_fingerprint_is_a_sha256_hex_and_field_boundaries_matter() -> None:
    value = compute_fingerprint("svc", "Err", "msg", "frame")
    assert len(value) == 64
    assert set(value) <= set("0123456789abcdef")
    # 단순 연결이면 두 값이 같아진다 — 구분자로 막는다.
    assert compute_fingerprint("ab", "c", "m", None) != compute_fingerprint("a", "bc", "m", None)


def test_fingerprint_is_deterministic_across_runs() -> None:
    records = [record(PLAIN_LINE), record(JSON_LINE, minutes=1)]
    first = [group.fingerprint for group in group_records(records, max_samples_per_group=1)]
    second = [group.fingerprint for group in group_records(records, max_samples_per_group=1)]
    assert first == second


# ------------------------------------------------------------------- 집계


def test_samples_are_capped_and_ordered_deterministically() -> None:
    records = [record(PLAIN_LINE, minutes=index) for index in range(7)]
    groups = group_records(records, max_samples_per_group=3)

    assert len(groups) == 1
    group = groups[0]
    assert group.count == 7
    assert len(group.samples) == 3
    assert [sample.occurred_at for sample in group.samples] == [at(0), at(1), at(2)]
    assert all(sample.masking_rule_version == MASKING_RULE_VERSION for sample in group.samples)


def test_groups_are_sorted_by_count_desc() -> None:
    records = [
        record("TimeoutError: gateway timed out", minutes=0),
        record("TimeoutError: gateway timed out", minutes=1),
        record("ValueError: bad order id", minutes=2),
    ]
    groups = group_records(records, max_samples_per_group=3)
    assert [group.count for group in groups] == [2, 1]
    assert groups[0].error_type == "TimeoutError"


def test_representative_labels_hold_only_what_the_whole_group_shares() -> None:
    """이 라벨로 Loki 를 재조회하므로, 그룹의 모든 레코드에 맞는 라벨만 남아야 한다."""
    records = [
        record(
            "TimeoutError: gateway timed out",
            labels={"service": "payment-api", "environment": "staging", "pod": "pay-1"},
            minutes=0,
        ),
        record(
            "TimeoutError: gateway timed out",
            labels={"service": "payment-api", "environment": "staging", "pod": "pay-2"},
            minutes=1,
        ),
    ]
    group = group_records(records, max_samples_per_group=3)[0]
    assert group.labels == {"service": "payment-api", "environment": "staging"}
    # 샘플에는 그 샘플의 라벨 전체가 남는다.
    assert group.samples[0].labels["pod"] == "pay-1"


def test_environment_is_dropped_when_the_group_spans_environments() -> None:
    """fingerprint 에 environment 가 없으므로 섞일 수 있다 — 섞였다면 단정하지 않는다."""
    records = [
        record("TimeoutError: gateway timed out", environment="staging", minutes=0),
        record("TimeoutError: gateway timed out", environment="production", minutes=1),
    ]
    groups = group_records(records, max_samples_per_group=3)
    assert len(groups) == 1
    assert groups[0].environment is None

    same = group_records([record("TimeoutError: x", environment="staging")], max_samples_per_group=1)
    assert same[0].environment == "staging"


def test_empty_input_returns_empty_list() -> None:
    assert group_records([], max_samples_per_group=3) == []


def test_max_samples_per_group_must_be_positive() -> None:
    with pytest.raises(ValueError):
        group_records([record(PLAIN_LINE)], max_samples_per_group=0)


def test_contract_shapes_round_trip() -> None:
    """정책 API 트랙이 이 모델을 그대로 직렬화한다."""
    group = group_records([record(JSON_LINE)], max_samples_per_group=1)[0]
    assert GroupedError.model_validate_json(group.model_dump_json()) == group
    assert isinstance(group.samples[0], GroupedSample)


# -------------------------------------------- 마스킹 → 정규화 → fingerprint


def test_pipeline_masks_before_normalizing() -> None:
    """샘플의 `masked_log` 는 이미 마스킹된 값이어야 한다 (DB 에 그대로 들어간다)."""
    group = group_records(
        [record("ERROR LoginError: bad password for admin@example.com")],
        max_samples_per_group=1,
    )[0]
    assert "admin@example.com" not in group.samples[0].masked_log
    assert "<MASKED:EMAIL>" in group.samples[0].masked_log
    assert "<MASKED:EMAIL>" in group.normalized_message


def test_secrets_never_appear_anywhere_in_the_result() -> None:
    """그룹 메시지·샘플·라벨 어디에도 원문 비밀값이 없어야 한다."""
    patterns = all_extra_patterns()
    records = []
    for index, fixture in enumerate(SECRET_FIXTURES):
        records.append(
            record(
                fixture.raw,
                minutes=index,
                labels={"service": "payment-api", "note": fixture.raw},
            )
        )
    groups = group_records(records, max_samples_per_group=5, extra_mask_patterns=patterns)

    serialized = json.dumps(
        [group.model_dump(mode="json") for group in groups], ensure_ascii=False
    )
    for fixture in SECRET_FIXTURES:
        for secret in fixture.secrets:
            assert secret not in serialized, f"{fixture.name}: {secret!r}"

    # 라벨 값도 마스킹을 거친다.
    for group in groups:
        for sample in group.samples:
            for value in sample.labels.values():
                for fixture in SECRET_FIXTURES:
                    for secret in fixture.secrets:
                        assert secret not in value


def test_masked_and_unmasked_input_produce_the_same_fingerprint() -> None:
    """마스킹 → 정규화 순서가 고정되어 있으므로, 이미 마스킹된 입력이 와도 같은 그룹이다."""
    raw = "ERROR LoginError: bad password for admin@example.com"
    pre_masked = "ERROR LoginError: bad password for <MASKED:EMAIL>"
    groups = group_records([record(raw), record(pre_masked, minutes=1)], max_samples_per_group=2)
    assert len(groups) == 1
    assert groups[0].count == 2
