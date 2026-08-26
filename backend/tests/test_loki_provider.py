"""`LokiProvider` 단위 테스트 — Loki HTTP API 는 respx 로 mock 한다.

여기서 지키려는 계약은 "Loki 고유 지식이 어댑터 밖으로 새지 않는다"이다.
`__error__` 라벨, 5,000 줄 한도, 나노초 타임스탬프, `count_over_time` 래핑은
전부 이 안에서만 다루고 밖으로는 `LogRecord`/`FetchResult`/`CountSeries` 만 나간다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.loki.provider import (
    LOKI_MAX_ENTRIES,
    LokiProvider,
    is_metric_query,
    resolve_label_mapping,
    wrap_count_over_time,
)
from app.providers.logsource import LogSourceError
from app.schemas.logrecord import TimeRange

BASE_URL = "http://loki.test:3100"
QUERY_RANGE_URL = f"{BASE_URL}/loki/api/v1/query_range"
LABELS_URL = f"{BASE_URL}/loki/api/v1/labels"
READY_URL = f"{BASE_URL}/ready"

START = datetime(2026, 8, 26, 10, 0, 0, tzinfo=UTC)
RANGE = TimeRange(start=START, end=START + timedelta(hours=1))
LOGQL = '{app="payment-api"} | json | level="ERROR"'


def make_provider(**overrides) -> LokiProvider:
    kwargs = {
        "base_url": BASE_URL,
        "auth_type": "none",
        "secret": None,
        "label_mapping": {},
        "timeout_seconds": 5.0,
    }
    kwargs.update(overrides)
    return LokiProvider(**kwargs)


def nanos(offset_seconds: int) -> str:
    return str(int((START + timedelta(seconds=offset_seconds)).timestamp()) * 1_000_000_000)


def streams_payload(result: list[dict]) -> dict:
    return {"status": "success", "data": {"resultType": "streams", "result": result}}


def matrix_payload(result: list[dict]) -> dict:
    return {"status": "success", "data": {"resultType": "matrix", "result": result}}


# ------------------------------------------------------------------ 순수 함수


def test_label_mapping_defaults_to_identity() -> None:
    assert resolve_label_mapping({}) == {
        "service": "service",
        "environment": "environment",
        "level": "level",
    }


def test_label_mapping_accepts_both_documented_directions() -> None:
    """freeze 문서 두 곳의 매핑 방향이 반대라 양쪽을 모두 받는다."""
    # models.py 방향: 표준 필드 -> 소스 라벨
    assert resolve_label_mapping({"service": "app", "level": "severity"}) == {
        "service": "app",
        "environment": "environment",
        "level": "severity",
    }
    # schemas/api.py 방향: 소스 라벨 -> 표준 필드
    assert resolve_label_mapping({"app": "service", "env": "environment"}) == {
        "service": "app",
        "environment": "env",
        "level": "level",
    }


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ('{app="x"}', False),
        ('{app="x"} | json | level="ERROR"', False),
        ('count_over_time({app="x"}[5m])', True),
        ('sum by (service) (count_over_time({app="x"}[5m]))', True),
        ('sum(rate({app="x"}[5m]))', True),
    ],
)
def test_is_metric_query(query: str, expected: bool) -> None:
    assert is_metric_query(query) is expected


def test_wrap_count_over_time() -> None:
    assert wrap_count_over_time('{app="x"}', 300) == 'sum(count_over_time({app="x"} [300s]))'
    already = 'sum by (service) (count_over_time({app="x"}[5m]))'
    assert wrap_count_over_time(already, 300) == already


# ----------------------------------------------------------------- fetch_logs


@respx.mock
def test_fetch_logs_parses_streams_into_log_records() -> None:
    route = respx.get(QUERY_RANGE_URL).mock(
        return_value=httpx.Response(
            200,
            json=streams_payload(
                [
                    {
                        "stream": {
                            "service": "payment-api",
                            "environment": "staging",
                            "level": "ERROR",
                        },
                        "values": [
                            [nanos(30), "TimeoutError: payment gateway timed out"],
                            [nanos(10), "TimeoutError: payment gateway timed out (retry)"],
                        ],
                    }
                ]
            ),
        )
    )

    result = make_provider().fetch_logs(LOGQL, RANGE, 100)

    assert result.fetched == 2
    assert result.dropped == 0
    assert result.truncated is False
    assert result.warnings == []
    assert [record.message for record in result.records] == [
        "TimeoutError: payment gateway timed out",
        "TimeoutError: payment gateway timed out (retry)",
    ]
    first = result.records[0]
    assert first.timestamp == START + timedelta(seconds=30)
    assert first.service == "payment-api"
    assert first.environment == "staging"
    assert first.level == "ERROR"
    assert first.labels["service"] == "payment-api"

    params = route.calls.last.request.url.params
    assert params["query"] == LOGQL
    assert params["direction"] == "backward"
    assert params["limit"] == "100"
    assert params["start"] == str(int(START.timestamp()) * 1_000_000_000)


@respx.mock
def test_fetch_logs_applies_label_mapping_to_standard_fields() -> None:
    respx.get(QUERY_RANGE_URL).mock(
        return_value=httpx.Response(
            200,
            json=streams_payload(
                [
                    {
                        "stream": {"app": "payment-api", "env": "prod", "severity": "ERROR"},
                        "values": [[nanos(1), "boom"]],
                    }
                ]
            ),
        )
    )

    provider = make_provider(
        label_mapping={"service": "app", "environment": "env", "level": "severity"}
    )
    record = provider.fetch_logs(LOGQL, RANGE, 10).records[0]

    assert (record.service, record.environment, record.level) == ("payment-api", "prod", "ERROR")
    # 원본 라벨은 그대로 남는다 (재조회용).
    assert record.labels == {"app": "payment-api", "env": "prod", "severity": "ERROR"}


@respx.mock
def test_fetch_logs_counts_error_label_streams_as_dropped() -> None:
    """`| json` 파싱 실패 줄(`__error__`)은 레코드로 올리지 않고 경고로만 보고한다."""
    respx.get(QUERY_RANGE_URL).mock(
        return_value=httpx.Response(
            200,
            json=streams_payload(
                [
                    {
                        "stream": {"service": "payment-api"},
                        "values": [[nanos(5), "정상 라인"]],
                    },
                    {
                        "stream": {
                            "service": "payment-api",
                            "__error__": "JSONParserErr",
                            "__error_details__": "invalid character",
                        },
                        "values": [
                            [nanos(4), "비정형 라인 1"],
                            [nanos(3), "비정형 라인 2"],
                        ],
                    },
                ]
            ),
        )
    )

    result = make_provider().fetch_logs(LOGQL, RANGE, 100)

    assert len(result.records) == 1
    assert result.records[0].message == "정상 라인"
    assert result.dropped == 2
    assert result.fetched == 3  # 소스에서 읽어온 총 라인 수
    codes = {warning.code for warning in result.warnings}
    assert codes == {"parse_error"}
    parse_warning = next(w for w in result.warnings if w.code == "parse_error")
    assert parse_warning.count == 2
    assert "JSONParserErr" in parse_warning.message


@respx.mock
def test_fetch_logs_marks_truncated_when_limit_reached() -> None:
    entries = [[nanos(i), f"line {i}"] for i in range(5)]
    respx.get(QUERY_RANGE_URL).mock(
        return_value=httpx.Response(
            200,
            json=streams_payload([{"stream": {"service": "s"}, "values": entries}]),
        )
    )

    result = make_provider().fetch_logs(LOGQL, RANGE, 5)

    assert result.truncated is True
    limit_warning = next(w for w in result.warnings if w.code == "limit_reached")
    assert limit_warning.count == 5


@respx.mock
def test_fetch_logs_clamps_limit_to_loki_max_entries() -> None:
    entries = [[nanos(0), "line"]]
    route = respx.get(QUERY_RANGE_URL).mock(
        return_value=httpx.Response(
            200,
            json=streams_payload([{"stream": {}, "values": entries}]),
        )
    )

    result = make_provider().fetch_logs(LOGQL, RANGE, 99_999)

    assert route.calls.last.request.url.params["limit"] == str(LOKI_MAX_ENTRIES)
    assert result.truncated is False


@respx.mock
def test_fetch_logs_rejects_metric_result_type() -> None:
    respx.get(QUERY_RANGE_URL).mock(return_value=httpx.Response(200, json=matrix_payload([])))
    with pytest.raises(LogSourceError):
        make_provider().fetch_logs("sum(count_over_time({a=\"b\"}[5m]))", RANGE, 10)


@respx.mock
def test_fetch_logs_wraps_http_error_in_log_source_error() -> None:
    respx.get(QUERY_RANGE_URL).mock(
        return_value=httpx.Response(400, text="parse error at line 1: syntax error")
    )
    with pytest.raises(LogSourceError) as excinfo:
        make_provider().fetch_logs(LOGQL, RANGE, 10)
    assert excinfo.value.status_code == 400
    assert "syntax error" in str(excinfo.value)


@respx.mock
def test_fetch_logs_wraps_transport_error() -> None:
    respx.get(QUERY_RANGE_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(LogSourceError):
        make_provider().fetch_logs(LOGQL, RANGE, 10)


# ------------------------------------------------------------ count_over_time


@respx.mock
def test_count_over_time_wraps_log_query_and_parses_matrix() -> None:
    route = respx.get(QUERY_RANGE_URL).mock(
        return_value=httpx.Response(
            200,
            json=matrix_payload(
                [
                    {
                        "metric": {"service": "payment-api"},
                        "values": [
                            [START.timestamp(), "3"],
                            [(START + timedelta(minutes=5)).timestamp(), "4.5"],
                        ],
                    }
                ]
            ),
        )
    )

    series = make_provider().count_over_time(LOGQL, RANGE, 300)

    assert route.calls.last.request.url.params["query"] == (
        f"sum(count_over_time({LOGQL} [300s]))"
    )
    assert route.calls.last.request.url.params["step"] == "300"
    assert series.step_seconds == 300
    assert series.total == pytest.approx(7.5)
    assert series.points[0].timestamp == START
    assert series.points[0].labels == {"service": "payment-api"}


@respx.mock
def test_count_over_time_passes_through_user_metric_query() -> None:
    metric_query = 'sum by (service) (count_over_time({app="x"}[1m]))'
    route = respx.get(QUERY_RANGE_URL).mock(
        return_value=httpx.Response(200, json=matrix_payload([]))
    )

    series = make_provider().count_over_time(metric_query, RANGE, 60)

    assert route.calls.last.request.url.params["query"] == metric_query
    assert series.points == []
    assert [warning.code for warning in series.warnings] == ["empty_result"]


@respx.mock
def test_count_over_time_rejects_stream_result() -> None:
    respx.get(QUERY_RANGE_URL).mock(return_value=httpx.Response(200, json=streams_payload([])))
    with pytest.raises(LogSourceError):
        make_provider().count_over_time(LOGQL, RANGE, 300)


# ------------------------------------------------------ test_connection / labels


@respx.mock
def test_test_connection_succeeds() -> None:
    respx.get(READY_URL).mock(return_value=httpx.Response(200, text="ready"))
    respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"status": "success", "data": ["app", "env"]})
    )

    result = make_provider().test_connection()

    assert result.ok is True
    assert result.latency_ms is not None and result.latency_ms >= 0
    assert result.details["ready"] is True
    assert result.details["label_count"] == 2


@respx.mock
def test_test_connection_reports_auth_failure() -> None:
    respx.get(READY_URL).mock(return_value=httpx.Response(200, text="ready"))
    respx.get(LABELS_URL).mock(return_value=httpx.Response(401, text="unauthorized"))

    result = make_provider(auth_type="bearer", secret="bad-token").test_connection()

    assert result.ok is False
    assert "401" in result.message


@respx.mock
def test_test_connection_reports_unreachable_host() -> None:
    respx.get(READY_URL).mock(side_effect=httpx.ConnectError("refused"))

    result = make_provider().test_connection()

    assert result.ok is False
    assert result.message


@respx.mock
def test_test_connection_tolerates_blocked_ready_endpoint() -> None:
    """`/ready` 가 프록시 뒤에서 404 여도 labels 가 되면 연결은 정상이다."""
    respx.get(READY_URL).mock(return_value=httpx.Response(404, text="not found"))
    respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"status": "success", "data": ["app"]})
    )

    result = make_provider().test_connection()

    assert result.ok is True
    assert result.details["ready"] is False


@respx.mock
def test_list_labels_returns_names_and_values() -> None:
    respx.get(LABELS_URL).mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": ["app", "env", "__error__"]}
        )
    )
    respx.get(f"{BASE_URL}/loki/api/v1/label/app/values").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": ["payment-api", "auth-api"]}
        )
    )
    respx.get(f"{BASE_URL}/loki/api/v1/label/env/values").mock(
        return_value=httpx.Response(500, text="boom")
    )

    labels = make_provider().list_labels()

    assert labels == {"app": ["payment-api", "auth-api"], "env": []}


# --------------------------------------------------------------------- 인증


@respx.mock
def test_bearer_auth_sets_authorization_header() -> None:
    route = respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"status": "success", "data": []})
    )
    make_provider(auth_type="bearer", secret="tok-123").list_labels()
    assert route.calls.last.request.headers["authorization"] == "Bearer tok-123"


@respx.mock
def test_basic_auth_sets_basic_header() -> None:
    route = respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"status": "success", "data": []})
    )
    make_provider(auth_type="basic", secret="admin:pw").list_labels()
    assert route.calls.last.request.headers["authorization"].startswith("Basic ")


def test_basic_auth_requires_user_colon_password() -> None:
    with pytest.raises(LogSourceError):
        make_provider(auth_type="basic", secret="no-colon").list_labels()


def test_unknown_auth_type_is_rejected() -> None:
    with pytest.raises(LogSourceError):
        make_provider(auth_type="oauth2", secret="x").list_labels()


def test_empty_base_url_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_provider(base_url="   ")
