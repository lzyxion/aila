"""정규화 레코드(`LogRecord`) fixture 와 비밀값 fixture 로더.

설계 문서의 두 지점을 그대로 옮긴 것이다.

- "`grouping` 이하 테스트는 **정규화 레코드 fixture** 기준으로 작성한다."
- "비밀값 포함 시나리오는 **원문 토큰이 없음을 단언하는 자동 테스트**로 만든다."

`secret_logs.jsonl` 은 사람이 읽고 늘릴 수 있는 데이터 파일이다. 한 줄이 한 시나리오이고,
`secrets` 에 적힌 문자열은 마스킹 결과 **어디에도** 남아 있으면 안 된다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.schemas.logrecord import LogRecord

FIXTURE_DIR = Path(__file__).resolve().parent
SECRET_LOG_FILE = FIXTURE_DIR / "secret_logs.jsonl"

#: 모든 fixture 가 기준으로 삼는 시각.
BASE_TIME = datetime(2026, 8, 26, 1, 0, 0, tzinfo=UTC)


def at(minutes: float = 0) -> datetime:
    """`BASE_TIME` 에서 `minutes` 분 뒤."""
    return BASE_TIME + timedelta(minutes=minutes)


def record(
    message: str,
    *,
    minutes: float = 0,
    service: str | None = "payment-api",
    environment: str | None = "staging",
    level: str | None = "ERROR",
    labels: dict[str, str] | None = None,
) -> LogRecord:
    """정규화 레코드 하나. 라벨 기본값은 표준 필드와 일치시킨다."""
    resolved_labels = labels
    if resolved_labels is None:
        resolved_labels = {}
        if service:
            resolved_labels["service"] = service
        if environment:
            resolved_labels["environment"] = environment
        if level:
            resolved_labels["level"] = level
    return LogRecord(
        timestamp=at(minutes),
        message=message,
        labels=resolved_labels,
        service=service,
        environment=environment,
        level=level,
    )


# ------------------------------------------------------------ 형식별 원본 라인

JSON_LINE = (
    '{"timestamp":"2026-08-26T01:00:00Z","level":"ERROR","service":"payment-api",'
    '"message":"Timeout while calling gateway for order 91823",'
    '"error_type":"java.net.SocketTimeoutException",'
    '"request_id":"8f2a1c3e-6d55-4f10-9a2b-0c1d2e3f4a5b",'
    '"stacktrace":"java.net.SocketTimeoutException: Read timed out\\n'
    "\\tat com.example.pay.GatewayClient.charge(GatewayClient.java:88)\\n"
    '\\tat com.example.pay.PaymentService.pay(PaymentService.java:42)"}'
)

LOGFMT_LINE = (
    'level=error service=payment-api msg="order 91823 failed after 1200ms" '
    "error=TimeoutError request_id=8f2a1c3e-6d55-4f10-9a2b-0c1d2e3f4a5b "
    "upstream=10.0.3.17:8443"
)

PLAIN_LINE = (
    "2026-08-26T01:00:00Z ERROR TimeoutError: payment gateway timed out after 3000ms "
    "(request_id=8f2a1c3e-6d55-4f10-9a2b-0c1d2e3f4a5b)"
)

PYTHON_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/app/payment/service.py", line 42, in charge\n'
    "    response = client.post(url, timeout=3)\n"
    '  File "/app/vendor/httpclient.py", line 118, in post\n'
    "    raise TimeoutError(message)\n"
    "TimeoutError: payment gateway timed out after 3000ms"
)

#: 같은 상위 프레임, 다른 하위 호출 경로 — 한 그룹이어야 한다.
PYTHON_TRACEBACK_OTHER_CALLER = (
    "Traceback (most recent call last):\n"
    '  File "/app/payment/service.py", line 42, in charge\n'
    "    response = client.post(url, timeout=3)\n"
    '  File "/app/batch/retry.py", line 7, in run\n'
    "    raise TimeoutError(message)\n"
    "TimeoutError: payment gateway timed out after 3000ms"
)

#: 다른 상위 프레임 — 다른 그룹이어야 한다.
PYTHON_TRACEBACK_OTHER_TOP_FRAME = (
    "Traceback (most recent call last):\n"
    '  File "/app/refund/service.py", line 91, in refund\n'
    "    response = client.post(url, timeout=3)\n"
    "TimeoutError: payment gateway timed out after 3000ms"
)

JAVA_STACKTRACE = (
    "java.lang.NullPointerException: Cannot invoke \"Order.getId()\" because order is null\n"
    "\tat com.example.pay.OrderMapper.map(OrderMapper.java:31)\n"
    "\tat com.example.pay.PaymentService.pay(PaymentService.java:42)\n"
    "\t... 24 more"
)


# --------------------------------------------------------------- 비밀값 fixture


@dataclass(frozen=True)
class SecretFixture:
    """비밀값이 들어 있는 로그 한 줄과, 결과에 남아서는 안 되는 문자열들."""

    name: str
    format: str
    raw: str
    extra_patterns: tuple[str, ...]
    secrets: tuple[str, ...]
    expected_kinds: tuple[str, ...]


def load_secret_fixtures() -> list[SecretFixture]:
    """`secret_logs.jsonl` 를 읽는다."""
    fixtures: list[SecretFixture] = []
    for line in SECRET_LOG_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        fixtures.append(
            SecretFixture(
                name=payload["name"],
                format=payload["format"],
                raw=payload["raw"],
                extra_patterns=tuple(payload.get("extra_patterns", ())),
                secrets=tuple(payload["secrets"]),
                expected_kinds=tuple(payload.get("expected_kinds", ())),
            )
        )
    return fixtures


def all_extra_patterns() -> tuple[str, ...]:
    """모든 비밀값 fixture 의 사용자 정의 규칙 합집합 (중복 제거, 순서 유지)."""
    seen: dict[str, None] = {}
    for fixture in load_secret_fixtures():
        for pattern in fixture.extra_patterns:
            seen.setdefault(pattern, None)
    return tuple(seen)


__all__ = [
    "BASE_TIME",
    "JAVA_STACKTRACE",
    "JSON_LINE",
    "LOGFMT_LINE",
    "PLAIN_LINE",
    "PYTHON_TRACEBACK",
    "PYTHON_TRACEBACK_OTHER_CALLER",
    "PYTHON_TRACEBACK_OTHER_TOP_FRAME",
    "SECRET_LOG_FILE",
    "SecretFixture",
    "all_extra_patterns",
    "at",
    "load_secret_fixtures",
    "record",
]
