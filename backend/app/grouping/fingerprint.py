"""fingerprint 계산.

`sha256(service + error_type + normalized_message + top_stack_frame)` 의 16 진 문자열.

네 조각은 유닛 구분자(`\\x1f`)로 이어 붙인다. 단순 연결이면 `("ab", "c")` 와
`("a", "bc")` 가 같은 값이 되어 서로 다른 오류가 한 그룹으로 뭉친다. 구분자는 로그
본문에 나타나지 않는 제어문자를 쓰므로 값 자체를 오염시키지 않는다.

fingerprint 는 **결정적**이어야 한다 — 조회 회차가 달라도 같은 오류는 같은 값이어야
"이미 분석했는가"를 fingerprint 기준으로 판정할 수 있다.
"""

from __future__ import annotations

import hashlib

#: 조각 구분자. 값을 바꾸면 기존 fingerprint 가 전부 달라지므로 규칙 버전을 올려야 한다.
FIELD_SEPARATOR = "\x1f"


def compute_fingerprint(
    service: str | None,
    error_type: str | None,
    normalized_message: str,
    top_stack_frame: str | None,
) -> str:
    """네 조각을 이어 sha256 16 진 문자열(64 자)을 만든다."""
    payload = FIELD_SEPARATOR.join(
        [
            service or "",
            error_type or "",
            normalized_message or "",
            top_stack_frame or "",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["FIELD_SEPARATOR", "compute_fingerprint"]
