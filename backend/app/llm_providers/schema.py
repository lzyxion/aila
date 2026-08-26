"""프로바이더 structured outputs 에 넘길 JSON Schema 정규화.

`app.schemas.analysis.analysis_json_schema()` 는 Pydantic 이 만든 스키마라
프로바이더의 strict 모드가 받지 않는 키워드(`minLength`, `minItems`, `minimum` …)를
포함하고, 기본값이 있는 필드는 `required` 에서 빠져 있다. OpenAI strict 모드와
Anthropic structured outputs 는 둘 다

- 모든 object 에 `additionalProperties: false`
- 모든 property 가 `required`
- 값 제약 키워드 미지원

을 요구하므로 여기서 한 번 변환한다.

**스키마 자체는 하나**여야 하므로(설계 문서 "검증 위치 계약") 변환은 프로바이더별로
다르게 하지 않고 이 모듈 하나만 쓴다. 제거되는 값 제약(`minItems: 1` 등)은
`app.schemas.analysis.parse_analysis_result()` 가 어댑터 밖에서 그대로 강제한다 —
즉 제약이 사라지는 게 아니라 검증 지점이 하나로 모인다.
"""

from __future__ import annotations

from typing import Any

#: strict 모드가 받지 않는 검증 키워드. 어댑터 밖 Pydantic 검증이 대신 강제한다.
UNSUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        # string
        "minLength",
        "maxLength",
        "pattern",
        "format",
        # number
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        # array
        "minItems",
        "maxItems",
        "uniqueItems",
        "contains",
        "minContains",
        "maxContains",
        "unevaluatedItems",
        # object
        "minProperties",
        "maxProperties",
        "patternProperties",
        "unevaluatedProperties",
        # 기타
        "default",
        "examples",
    }
)

#: `$defs` 처럼 "값이 스키마들의 map" 인 키.
_SCHEMA_MAP_KEYS: frozenset[str] = frozenset({"properties", "$defs", "definitions"})


def to_strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """구조화 출력용 strict 스키마로 변환한다 (원본은 건드리지 않는다)."""
    normalized = _normalize(schema)
    if not isinstance(normalized, dict):  # pragma: no cover - 방어
        raise ValueError("json_schema 는 object 스키마여야 합니다.")
    return normalized


def _normalize(node: Any) -> Any:
    if isinstance(node, list):
        return [_normalize(item) for item in node]
    if not isinstance(node, dict):
        return node

    # `$ref` 옆의 형제 키(description 등)를 거부하는 구현이 있어 ref 만 남긴다.
    if "$ref" in node:
        return {"$ref": node["$ref"]}

    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in UNSUPPORTED_KEYWORDS:
            continue
        if key in _SCHEMA_MAP_KEYS and isinstance(value, dict):
            result[key] = {name: _normalize(sub) for name, sub in value.items()}
        else:
            result[key] = _normalize(value)

    properties = result.get("properties")
    if isinstance(properties, dict):
        result.setdefault("type", "object")
        result["additionalProperties"] = False
        # strict 모드는 "모든 property 가 required" 를 요구한다.
        result["required"] = list(properties.keys())

    return result


__all__ = ["UNSUPPORTED_KEYWORDS", "to_strict_json_schema"]
