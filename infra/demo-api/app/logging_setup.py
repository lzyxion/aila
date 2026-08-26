"""demo-api 구조화 로그 출력.

stdout 으로 **한 줄 = 한 JSON** 을 내보낸다. Alloy 가 docker stdout 을 읽어
`| json` 으로 파싱하므로, 여러 줄에 걸친 로그는 만들지 않는다 —
스택트레이스도 `stacktrace` 필드 안에 `\\n` 이 든 문자열 하나로 담는다.

동시에 `emit_raw()` 로 **비정형(non-JSON) 라인**도 일부러 섞는다. LogQL 의 `| json`
파서는 실패한 줄에 `__error__` 라벨을 붙이고 통과시키므로, 뒤따르는 `level="ERROR"`
필터가 그 줄을 통째로 걸러낸다. 이 상황을 데모 환경에서 실제로 재현해 두어야
AILA 백엔드가 파싱 실패 건수를 `dropped` / `warnings` 로 올리는 것을 검증할 수 있다.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
import uuid
from datetime import UTC, datetime
from typing import Any

SERVICE_NAME = os.getenv("DEMO_SERVICE_NAME", "payment-api")
ENVIRONMENT = os.getenv("DEMO_ENVIRONMENT", "staging")

# 로그 필드 순서를 고정한다. 사람이 docker logs 로 볼 때 읽기 쉽고,
# 시나리오 기준선(expected-analysis.md) 과 diff 하기도 쉽다.
_FIELD_ORDER = (
    "timestamp",
    "service",
    "environment",
    "level",
    "release",
    "request_id",
    "message",
    "exception",
    "stacktrace",
)

# stdout 은 여러 스레드/태스크가 함께 쓴다. 줄이 섞이면 JSON 이 깨진다.
_write_lock = threading.Lock()


def new_request_id() -> str:
    """가변값. 그룹화 정규화 단계가 반드시 지워야 하는 값이다."""
    return f"req-{uuid.uuid4()}"


def _now() -> str:
    # RFC3339Nano. Alloy 의 stage.timestamp 가 이 포맷으로 파싱한다.
    return datetime.now(UTC).isoformat()


def _write(line: str) -> None:
    with _write_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def emit(
    level: str,
    message: str,
    *,
    service: str | None = None,
    environment: str | None = None,
    release: str | None = None,
    request_id: str | None = None,
    exception: str | None = None,
    stacktrace: str | None = None,
    **extra: Any,
) -> None:
    """구조화 JSON 로그 한 줄.

    `service` 를 넘기면 컨테이너 라벨의 기본 service 를 덮어쓴다 — Alloy 의
    `stage.labels` 가 JSON 본문 값을 우선하므로, 컨테이너 하나가
    payment-api / order-api / auth-api 세 스트림을 동시에 만든다.
    """
    record: dict[str, Any] = {
        "timestamp": _now(),
        "service": service or SERVICE_NAME,
        "environment": environment or ENVIRONMENT,
        "level": level.upper(),
        "release": release,
        "request_id": request_id,
        "message": message,
        "exception": exception,
        "stacktrace": stacktrace,
    }
    record.update(extra)

    ordered = {key: record[key] for key in _FIELD_ORDER if key in record}
    ordered.update({k: v for k, v in record.items() if k not in _FIELD_ORDER})

    # None 필드는 빼지 않고 남긴다. 필드 존재 자체가 스키마의 일부이고,
    # 백엔드 파서가 null 을 어떻게 다루는지도 이 데모로 검증 대상이다.
    _write(json.dumps(ordered, ensure_ascii=False))


def emit_raw(line: str) -> None:
    """비정형 라인. `| json` 이 여기서 실패하는 것이 이 함수의 목적이다."""
    _write(line)


def format_exception(exc: BaseException) -> tuple[str, str]:
    """(예외 타입 이름, 스택트레이스 문자열) 을 돌려준다.

    스택트레이스는 개행이 든 **한 문자열**이다. 여러 줄로 출력하면 Alloy 가
    줄마다 별개 엔트리로 읽어 그룹화가 무의미해진다.
    """
    stack = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    ).rstrip()
    return type(exc).__name__, stack
