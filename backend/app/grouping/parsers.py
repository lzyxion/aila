"""로그 라인 파싱 — JSON / logfmt / 비정형을 구분해 메시지·예외 타입·스택트레이스를 뽑는다.

입력은 **이미 마스킹된** 로그 라인이다(계약 순서: 마스킹 → 정규화 → fingerprint).
마스킹 플레이스홀더에는 따옴표·백슬래시가 없으므로 JSON/logfmt 구조는 마스킹 후에도
그대로 파싱된다.

파싱은 규칙 기반이라 완벽할 수 없다. 실패하면 예외를 던지지 않고 `plain` 으로 떨어져
메시지 전체를 그대로 쓴다 — 조회 한 건이 파싱 실패로 통째로 날아가는 편이 더 나쁘다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

FORMAT_JSON = "json"
FORMAT_LOGFMT = "logfmt"
FORMAT_PLAIN = "plain"


@dataclass(frozen=True)
class ParsedLog:
    """한 로그 라인에서 뽑아낸 것들 (정규화 전)."""

    format: str
    message: str
    error_type: str | None = None
    stacktrace: str | None = None
    top_stack_frame: str | None = None
    fields: dict[str, str] = field(default_factory=dict)


# ------------------------------------------------------------------ 필드 이름

_MESSAGE_KEYS = (
    "message",
    "msg",
    "log",
    "event",
    "error_message",
    "errormessage",
    "err_msg",
    "detail",
    "description",
    "reason",
    "text",
    "body",
    "short_message",
    "exception",
    "error",
    "err",
)

_ERROR_TYPE_KEYS = (
    "error_type",
    "errortype",
    "exception_type",
    "exceptiontype",
    "exc_type",
    "exception_class",
    "error_class",
    "error.type",
    "exception.type",
    "error.class",
    "exception.class",
    "throwable",
    "kind",
    "type",
    "exception",
    "error",
)

_STACK_KEYS = (
    "stacktrace",
    "stack_trace",
    "stacktrace_text",
    "stack",
    "traceback",
    "exception_stacktrace",
    "exception.stacktrace",
    "error.stack",
    "error.stack_trace",
    "exception.stack_trace",
    "exc_info",
)


# ------------------------------------------------------------- 예외 타입 추출

_ERROR_TYPE_RE = re.compile(
    r"(?<![\w.$])"
    r"((?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Z][A-Za-z0-9_]*"
    r"(?:Error|Exception|Failure|Fault|Timeout|Panic|Interrupt|Refused|Denied|Overflow))"
    r"(?![\w])"
)

#: 단독으로 쓰였을 때 예외 타입으로 인정하는 형태 (`org.x.FooException`, `FooError`).
_BARE_TYPE_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*$")


def extract_error_type(text: str | None) -> str | None:
    """메시지 본문에서 예외 타입을 찾는다. 콜론이 뒤따르는 후보를 우선한다."""
    if not text:
        return None
    first: str | None = None
    for match in _ERROR_TYPE_RE.finditer(text):
        if first is None:
            first = match.group(1)
        tail = text[match.end() : match.end() + 1]
        if tail in {":", "(", ",", ""}:
            return match.group(1)
    return first


# --------------------------------------------------------------- 스택트레이스

#: Java / Node / .NET — 가장 안쪽(오류 지점) 프레임이 **먼저** 출력된다.
_AT_FRAME_RE = re.compile(r"^\s*at\s+\S+")
#: Python — 바깥(호출자)에서 안쪽(오류 지점) 순으로 출력된다.
_PY_FRAME_RE = re.compile(r"""^\s*File\s+"[^"]*",\s*line\s+\d+""")
#: Python 트레이스백 헤더. 이게 있으면 프레임 순서가 바깥 -> 안쪽이다.
_PY_HEADER_RE = re.compile(r"^\s*Traceback \(most recent call last\)\s*:\s*$")

_STACK_FRAME_PATTERNS = (
    _AT_FRAME_RE,
    _PY_FRAME_RE,
    re.compile(r"^\s*\.{3}\s*\d+\s+more\s*$"),  # Java "... 12 more"
    re.compile(r"^\s+\S+\.(?:go|rb|php|kt|scala|cs|js|ts):\d+"),  # Go/Ruby/PHP 등
    re.compile(r"^\s*#\d+\s+\S+"),  # PHP `#0 /app/x.php(12): foo()`
    re.compile(r"^\s*from\s+\S+:\d+:in\s+"),  # Ruby
)

_STACK_HEADER_PATTERNS = (
    _PY_HEADER_RE,
    re.compile(r"^\s*Caused by\s*:"),
    re.compile(r"^\s*The above exception was the direct cause"),
    re.compile(r"^\s*During handling of the above exception"),
)


def _is_frame(line: str) -> bool:
    return any(pattern.match(line) for pattern in _STACK_FRAME_PATTERNS)


def _is_stack_header(line: str) -> bool:
    return any(pattern.match(line) for pattern in _STACK_HEADER_PATTERNS)


def _is_python_traceback(lines: list[str]) -> bool:
    """Python 형식인가 — `Traceback (...)` 헤더가 있거나, `File "...", line N` 만 있는가."""
    if any(_PY_HEADER_RE.match(line) for line in lines):
        return True
    return any(_PY_FRAME_RE.match(line) for line in lines) and not any(
        _AT_FRAME_RE.match(line) for line in lines
    )


def top_stack_frame(stacktrace: str | None) -> str | None:
    """스택트레이스에서 **오류가 난 프레임 한 줄만** 돌려준다.

    전체를 fingerprint 에 넣으면 호출 경로가 조금만 달라도 같은 버그가 쪼개지고,
    메시지만 넣으면 다른 원인이 뭉친다 — 설계 문서가 상위 프레임만 쓰라고 못 박은 이유다.

    >>> 언어마다 프레임 순서가 반대다 <<<
    Java/Node 의 `at ...` 는 **가장 안쪽(오류 지점) 프레임이 먼저** 나오지만, Python
    트레이스백은 `most recent call last` 라는 이름 그대로 **바깥(호출자) -> 안쪽** 순으로
    나온다. 그래서 Python 은 **마지막** `File` 줄이 오류 지점이다. 첫 줄을 쓰면 진입점
    (예: 웹 프레임워크의 핸들러)만 잡혀, 서로 다른 오류가 한 그룹으로 뭉친다.
    """
    if not stacktrace:
        return None
    lines = stacktrace.splitlines()

    if _is_python_traceback(lines):
        python_frames = [line for line in lines if _PY_FRAME_RE.match(line)]
        if python_frames:
            return python_frames[-1].strip()

    for line in lines:
        if _is_frame(line):
            return line.strip()
    for line in lines:
        stripped = line.strip()
        if stripped and not _is_stack_header(line):
            return stripped
    return None


def split_message_and_stack(text: str) -> tuple[str, str | None]:
    """여러 줄 로그를 (메시지, 스택트레이스) 로 나눈다."""
    if not text:
        return "", None
    lines = text.splitlines()
    if len(lines) <= 1:
        return text.strip(), None

    start = next(
        (i for i, line in enumerate(lines) if _is_frame(line) or _is_stack_header(line)),
        None,
    )
    if start is None:
        return text.strip(), None

    head = "\n".join(lines[:start]).strip()
    stack = "\n".join(lines[start:]).strip()

    if head:
        return head, stack or None

    # Python 트레이스백처럼 예외 줄이 블록의 **끝**에 오는 형태.
    for line in reversed(lines[start:]):
        stripped = line.strip()
        if not stripped or _is_frame(line) or _is_stack_header(line):
            continue
        return stripped, stack or None

    return lines[start].strip(), stack or None


# --------------------------------------------------------------------- JSON

def _flatten(obj: dict[str, Any], prefix: str = "", depth: int = 0) -> dict[str, str]:
    flat: dict[str, str] = {}
    if depth > 4:
        return flat
    for key, value in obj.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{name}.", depth + 1))
        elif isinstance(value, list):
            flat[name] = "\n".join(
                str(item) for item in value if not isinstance(item, (dict, list))
            )
        elif value is None or isinstance(value, bool):
            flat[name] = "" if value is None else str(value)
        else:
            flat[name] = str(value)
    return flat


def _lookup(fields: dict[str, str], keys: tuple[str, ...]) -> tuple[str, str] | None:
    lowered = {key.lower(): key for key in fields}
    for candidate in keys:
        actual = lowered.get(candidate)
        if actual is not None and fields[actual].strip():
            return actual, fields[actual]
    return None


def _parse_json(raw: str) -> ParsedLog | None:
    stripped = raw.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return None
    try:
        payload = json.loads(stripped)
    except (ValueError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None

    fields = _flatten(payload)
    message_hit = _lookup(fields, _MESSAGE_KEYS)
    message = message_hit[1] if message_hit else stripped

    stack_hit = _lookup(fields, _STACK_KEYS)
    stacktrace = stack_hit[1] if stack_hit else None

    error_type = None
    type_hit = _lookup(fields, _ERROR_TYPE_KEYS)
    if type_hit:
        candidate = type_hit[1].strip()
        if _BARE_TYPE_RE.match(candidate):
            error_type = candidate
        else:
            error_type = extract_error_type(candidate)
    if error_type is None:
        error_type = extract_error_type(message) or extract_error_type(stacktrace)

    if stacktrace is None:
        message, stacktrace = split_message_and_stack(message)

    return ParsedLog(
        format=FORMAT_JSON,
        message=message.strip(),
        error_type=error_type,
        stacktrace=stacktrace,
        top_stack_frame=top_stack_frame(stacktrace),
        fields=fields,
    )


# ------------------------------------------------------------------- logfmt

_LOGFMT_PAIR = re.compile(r"""([A-Za-z_][\w.\-]*)=("(?:[^"\\]|\\.)*"|[^\s]*)""")
_LOGFMT_LINE = re.compile(
    r"""^\s*(?:[A-Za-z_][\w.\-]*=(?:"(?:[^"\\]|\\.)*"|[^\s]*)\s*)+$"""
)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
    return value


def _parse_logfmt(raw: str) -> ParsedLog | None:
    line = raw.strip()
    if "=" not in line or "\n" in line:
        return None
    if not _LOGFMT_LINE.match(line):
        return None
    pairs = _LOGFMT_PAIR.findall(line)
    if len(pairs) < 2:
        return None

    fields = {key: _unquote(value) for key, value in pairs}

    message_hit = _lookup(fields, _MESSAGE_KEYS)
    message = message_hit[1] if message_hit else line

    stack_hit = _lookup(fields, _STACK_KEYS)
    stacktrace = stack_hit[1] if stack_hit else None

    error_type = None
    type_hit = _lookup(fields, _ERROR_TYPE_KEYS)
    if type_hit:
        candidate = type_hit[1].strip()
        error_type = candidate if _BARE_TYPE_RE.match(candidate) else extract_error_type(candidate)
    if error_type is None:
        error_type = extract_error_type(message) or extract_error_type(stacktrace)

    if stacktrace is None:
        message, stacktrace = split_message_and_stack(message)

    return ParsedLog(
        format=FORMAT_LOGFMT,
        message=message.strip(),
        error_type=error_type,
        stacktrace=stacktrace,
        top_stack_frame=top_stack_frame(stacktrace),
        fields=fields,
    )


# --------------------------------------------------------------------- plain

_LEVELS = r"TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL|SEVERE"
_PLAIN_PREFIX_RE = re.compile(
    r"^\s*(?:\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\]?\s*)?"
    rf"(?:(?:\[(?:{_LEVELS})\]|\b(?:{_LEVELS})\b)\s*[:\-]?\s*)?",
    re.IGNORECASE,
)


def _parse_plain(raw: str) -> ParsedLog:
    message, stacktrace = split_message_and_stack(raw)
    body = _PLAIN_PREFIX_RE.sub("", message, count=1).strip() or message.strip()
    error_type = extract_error_type(body) or extract_error_type(stacktrace)
    return ParsedLog(
        format=FORMAT_PLAIN,
        message=body,
        error_type=error_type,
        stacktrace=stacktrace,
        top_stack_frame=top_stack_frame(stacktrace),
        fields={},
    )


def parse_line(raw: str) -> ParsedLog:
    """JSON → logfmt → 비정형 순으로 시도한다. 어떤 입력이 와도 예외를 던지지 않는다."""
    if not raw or not raw.strip():
        return ParsedLog(format=FORMAT_PLAIN, message="")
    return _parse_json(raw) or _parse_logfmt(raw) or _parse_plain(raw)


__all__ = [
    "FORMAT_JSON",
    "FORMAT_LOGFMT",
    "FORMAT_PLAIN",
    "ParsedLog",
    "extract_error_type",
    "parse_line",
    "split_message_and_stack",
    "top_stack_frame",
]
