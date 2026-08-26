"""요청의 `response_format.json_schema` 를 읽어 스키마에 맞는 JSON 을 만든다.

목적은 "그럴듯한 문장"이 아니라 **파이프라인이 끝까지 도는 것**이다. 백엔드는 어댑터
바깥 공통 경로에서 `AnalysisResultSchema` 로 응답을 검증하므로(설계 문서 "검증 위치
계약"), 여기서 스키마를 어기면 E2E 는 그 지점에서 멈춘다. 그래서 필드 목록을 하드코딩
하지 않고 **요청에 실려 온 스키마를 그대로 순회**해서 값을 채운다 — 백엔드가
`AnalysisResultSchema` 를 바꾸면 이 스텁도 따라 바뀐다.

내용은 프롬프트에서 뽑은 몇 가지 사실(서비스명·예외 타입·발생 횟수·HTTP 상태)로만
채운다. **프롬프트 본문을 그대로 되돌려주지 않는다** — 되돌려주면 마스킹 감사가
llm-mock 응답을 통해 우회될 여지가 생긴다.
"""

from __future__ import annotations

import re
from typing import Any

#: 백엔드 마스킹이 남기는 치환 표식. 프롬프트에 이게 있으면 마스킹이 걸린 것이다.
MASK_MARK = "<MASKED:"

_EXCEPTION_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Timeout|Failure))\b")
_SERVICE_RE = re.compile(r"^-\s*서비스:\s*(.+?)\s*$", re.MULTILINE)
_ENVIRONMENT_RE = re.compile(r"^-\s*환경:\s*(.+?)\s*$", re.MULTILINE)
_ERROR_TYPE_RE = re.compile(r"^-\s*예외 타입:\s*(.+?)\s*$", re.MULTILINE)
_COUNT_RE = re.compile(r"^-\s*발생 횟수:\s*(\d+)", re.MULTILINE)
_STATUS_RE = re.compile(r"\b(?:http_)?status[=:\s]+([1-5]\d\d)\b")

_UNKNOWN = ("알 수 없음", "", "None", "null")


def extract_facts(prompt: str) -> dict[str, Any]:
    """프롬프트에서 응답 문장에 쓸 사실만 추린다 (원문은 보관하지 않는다)."""

    def _one(pattern: re.Pattern[str]) -> str | None:
        match = pattern.search(prompt)
        if match is None:
            return None
        value = match.group(1).strip()
        return None if value in _UNKNOWN else value

    error_type = _one(_ERROR_TYPE_RE)
    if error_type is None:
        found = _EXCEPTION_RE.search(prompt)
        error_type = found.group(1) if found else None

    count_match = _COUNT_RE.search(prompt)
    status_match = _STATUS_RE.search(prompt)

    return {
        "service": _one(_SERVICE_RE) or "알 수 없는 서비스",
        "environment": _one(_ENVIRONMENT_RE) or "알 수 없는 환경",
        "error_type": error_type or "미상 예외",
        "count": int(count_match.group(1)) if count_match else 0,
        "http_status": int(status_match.group(1)) if status_match else None,
        "masked": MASK_MARK in prompt,
    }


# ---------------------------------------------------------------------------
# 필드별 문장 (한국어)
# ---------------------------------------------------------------------------


def _summary(facts: dict[str, Any], index: int) -> str:
    status = f", HTTP {facts['http_status']}" if facts["http_status"] else ""
    return (
        f"{facts['service']}({facts['environment']}) 에서 {facts['error_type']} 가 "
        f"{facts['count']}건 반복 발생하고 있습니다{status}. "
        "[llm-mock 이 생성한 데모 응답]"
    )


def _cause(facts: dict[str, Any], index: int) -> str:
    options = [
        f"업스트림 의존 구간의 응답 지연 또는 연결 실패로 {facts['error_type']} 가 "
        "전파되고 있을 가능성이 있습니다.",
        f"{facts['service']} 의 재시도·타임아웃 설정이 업스트림 지연 특성과 맞지 않아 "
        "실패가 증폭되고 있을 가능성이 있습니다.",
        "직전 배포나 설정 변경으로 특정 코드 경로에서만 예외가 발생하고 있을 가능성이 "
        "있습니다.",
        "커넥션 풀·스레드 풀 등 공유 자원 고갈로 요청이 대기하다 실패했을 가능성이 "
        "있습니다.",
    ]
    return options[index % len(options)]


def _evidence(facts: dict[str, Any], index: int) -> str:
    options = [
        f"대표 로그의 예외 타입이 {facts['error_type']} 로 동일합니다.",
        f"같은 fingerprint 로 묶인 발생 횟수가 {facts['count']}건입니다.",
        (
            "대표 로그의 민감정보가 <MASKED:...> 로 치환되어 있어 원문 값은 확인할 수 "
            "없습니다."
            if facts["masked"]
            else "대표 로그의 메시지 형태가 그룹 전체에서 일정합니다."
        ),
    ]
    return options[index % len(options)]


def _investigation_step(facts: dict[str, Any], index: int) -> str:
    options = [
        f"같은 기간의 {facts['service']} 업스트림 응답 시간·오류율 지표를 확인합니다.",
        "오류 그룹의 발생 추이가 특정 시각에 계단식으로 올라갔는지 확인합니다.",
        f"{facts['environment']} 환경의 최근 배포 이력과 오류 시작 시각을 대조합니다.",
        "커넥션 풀 사용률과 타임아웃 설정값을 확인합니다.",
    ]
    return options[index % len(options)]


def _mitigation(facts: dict[str, Any], index: int) -> str:
    options = [
        "업스트림 타임아웃과 재시도 횟수를 조정하고 지수 백오프를 적용합니다.",
        "영향 범위가 확인되면 직전 릴리스로 롤백하는 방안을 검토합니다.",
        "회로 차단기(circuit breaker)를 두어 실패 증폭을 막습니다.",
    ]
    return options[index % len(options)]


def _limitation(facts: dict[str, Any], index: int) -> str:
    options = [
        "이 분석은 마스킹된 대표 로그 몇 건만 근거로 하므로 실제 원인을 단정할 수 "
        "없습니다.",
        "업스트림 서비스의 로그·지표가 없어 장애 지점이 자사인지 외부인지 구분할 수 "
        "없습니다.",
        "요청 본문·사용자 식별자는 마스킹되어 있어 특정 테넌트에 국한된 문제인지 알 수 "
        "없습니다.",
        "이 응답은 llm-mock 이 만든 데모 데이터이며 실제 모델 추론 결과가 아닙니다.",
    ]
    return options[index % len(options)]


#: 프로퍼티 이름 → 문장 생성기. 이름이 없으면 일반 문장을 쓴다.
_TEXT: dict[str, Any] = {
    "summary": _summary,
    "cause": _cause,
    "evidence": _evidence,
    "investigation_steps": _investigation_step,
    "mitigation": _mitigation,
    "limitations": _limitation,
}

#: 배열 프로퍼티 → 만들 항목 수. `min_length=1` 계약을 만족해야 하므로 최소 1 이다.
_ARRAY_LEN: dict[str, int] = {
    "hypotheses": 3,
    "evidence": 3,
    "investigation_steps": 4,
    "mitigation": 3,
    "limitations": 4,
}
_DEFAULT_ARRAY_LEN = 2

#: 신뢰도. 정렬용 힌트일 뿐이므로 내림차순으로만 준다.
_CONFIDENCE = (0.62, 0.41, 0.28, 0.2)


def _severity(facts: dict[str, Any], allowed: list[Any]) -> Any:
    count = facts["count"]
    status = facts["http_status"] or 0
    if count >= 100:
        preferred = ["critical", "high", "medium"]
    elif count >= 20 or status >= 500:
        preferred = ["high", "medium", "critical"]
    elif status >= 400:
        preferred = ["medium", "low", "high"]
    else:
        preferred = ["medium", "low", "info"]
    for value in preferred:
        if value in allowed:
            return value
    return allowed[0]


# ---------------------------------------------------------------------------
# 스키마 순회
# ---------------------------------------------------------------------------


def _resolve(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """`$ref` 를 루트의 `$defs`/`definitions` 에서 찾아 펼친다."""
    seen = 0
    while isinstance(node, dict) and "$ref" in node:
        ref = str(node["$ref"])
        if not ref.startswith("#/"):
            raise ValueError(f"외부 $ref 는 지원하지 않습니다: {ref}")
        target: Any = root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                raise ValueError(f"$ref 를 찾을 수 없습니다: {ref}")
            target = target[part]
        node = target
        seen += 1
        if seen > 20:  # 순환 방어
            raise ValueError(f"$ref 순환으로 보입니다: {ref}")
    return node


def _fallback_text(name: str, index: int) -> str:
    label = name or "값"
    return f"{label} 항목 {index + 1} (llm-mock 데모 값)"


def generate(
    schema: dict[str, Any],
    facts: dict[str, Any],
    *,
    root: dict[str, Any] | None = None,
    name: str = "",
    index: int = 0,
    depth: int = 0,
) -> Any:
    """스키마 한 노드에 해당하는 값을 만든다."""
    root = root if root is not None else schema
    if depth > 12:
        return None
    node = _resolve(schema, root)
    if not isinstance(node, dict):
        return None

    for key in ("allOf", "anyOf", "oneOf"):
        branches = node.get(key)
        if isinstance(branches, list) and branches:
            for branch in branches:
                resolved = _resolve(branch, root)
                if isinstance(resolved, dict) and resolved.get("type") != "null":
                    return generate(
                        resolved, facts, root=root, name=name, index=index, depth=depth + 1
                    )

    if "const" in node:
        return node["const"]

    enum = node.get("enum")
    if isinstance(enum, list) and enum:
        if name == "severity" or "severity" in str(node.get("title", "")).lower():
            return _severity(facts, enum)
        return enum[index % len(enum)]

    node_type = node.get("type")
    if isinstance(node_type, list):
        node_type = next((item for item in node_type if item != "null"), None)

    if node_type == "object" or "properties" in node:
        properties = node.get("properties")
        if not isinstance(properties, dict):
            return {}
        return {
            key: generate(sub, facts, root=root, name=key, index=index, depth=depth + 1)
            for key, sub in properties.items()
        }

    if node_type == "array" or "items" in node:
        items = node.get("items")
        if not isinstance(items, dict):
            return []
        length = _ARRAY_LEN.get(name, _DEFAULT_ARRAY_LEN)
        return [
            generate(items, facts, root=root, name=name, index=i, depth=depth + 1)
            for i in range(length)
        ]

    if node_type == "integer":
        return 1
    if node_type == "number":
        if name == "confidence":
            return _CONFIDENCE[index % len(_CONFIDENCE)]
        return 0.5
    if node_type == "boolean":
        return False
    if node_type == "null":
        return None

    # 문자열(그리고 타입이 없는 노드)
    builder = _TEXT.get(name)
    if builder is not None:
        return builder(facts, index)
    return _fallback_text(name, index)


def build_analysis_json(schema: dict[str, Any], prompt: str) -> dict[str, Any]:
    """분석 요청 한 건에 대한 응답 본문(dict)."""
    facts = extract_facts(prompt)
    result = generate(schema, facts)
    if not isinstance(result, dict):
        raise ValueError("스키마 루트가 object 가 아닙니다.")
    return result


__all__ = ["MASK_MARK", "build_analysis_json", "extract_facts", "generate"]
