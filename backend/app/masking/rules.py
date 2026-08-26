"""마스킹 규칙 정의 (`MASKING_RULE_VERSION = "v1"`).

규칙은 **순서가 있는 목록**이다. 앞 규칙이 먼저 치환하므로, 더 넓은 문맥을 먹는 규칙
(DB 연결 문자열, Authorization 헤더)을 좁은 규칙(이메일, 숫자)보다 앞에 둔다.

모든 치환 결과는 `<MASKED:종류>` 플레이스홀더다. 플레이스홀더 자체에는 숫자·`@`·`://`
가 없으므로 뒤따르는 규칙과 정규화(`app.grouping.normalize`)가 다시 건드리지 않는다 —
`mask()` 가 **멱등**이어야 하는 이유는 설계상 마스킹을 화면 표시 전과 LLM 전송 직전에
두 번 걸기 때문이다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

# --------------------------------------------------------------- 플레이스홀더

PLACEHOLDER_FORMAT = "<MASKED:{kind}>"

#: 플레이스홀더 전체를 찾아내는 패턴 (테스트·검증용).
PLACEHOLDER_RE = re.compile(r"<MASKED:[A-Z_]+>")

KIND_API_KEY = "API_KEY"
KIND_BEARER_TOKEN = "BEARER_TOKEN"
KIND_JWT = "JWT"
KIND_PASSWORD = "PASSWORD"
KIND_COOKIE = "COOKIE"
KIND_DB_URI = "DB_URI"
KIND_URI_CREDENTIALS = "URI_CREDENTIALS"
KIND_EMAIL = "EMAIL"
KIND_PHONE = "PHONE"
KIND_CARD = "CARD"
KIND_CUSTOM = "CUSTOM"

#: `mask()` 가 만들어 낼 수 있는 종류 전체.
KINDS: tuple[str, ...] = (
    KIND_API_KEY,
    KIND_BEARER_TOKEN,
    KIND_JWT,
    KIND_PASSWORD,
    KIND_COOKIE,
    KIND_DB_URI,
    KIND_URI_CREDENTIALS,
    KIND_EMAIL,
    KIND_PHONE,
    KIND_CARD,
    KIND_CUSTOM,
)


def placeholder(kind: str) -> str:
    """`<MASKED:종류>` 문자열을 만든다."""
    return PLACEHOLDER_FORMAT.format(kind=kind)


@dataclass(frozen=True)
class MaskRule:
    """규칙 하나. `replacement` 는 `re.sub` 템플릿 또는 콜러블이다."""

    kind: str
    pattern: re.Pattern[str]
    replacement: str | Callable[[re.Match[str]], str]

    def apply(self, text: str) -> str:
        return self.pattern.sub(self.replacement, text)


# ------------------------------------------------------------------ 공용 조각

#: `key: "value"` / `key=value` / `"key": "value"` 사이의 구분자.
_SEP = r"""["']?\s*[:=]\s*["']?"""
#: 따옴표·구분 문자 앞에서 멈추는 비밀값 본문.
_VALUE = r"""[^\s"',;&}\)\]]+"""
#: 헤더 한 줄 전체를 값으로 보는 경우 (따옴표 안이면 따옴표에서 멈춘다).
_LINE_VALUE = r"""[^"'\r\n]*"""

_I = re.IGNORECASE


def _luhn_ok(digits: str) -> bool:
    """카드번호 Luhn 체크. 긴 숫자 ID 를 카드로 오인해 통째로 지우는 것을 줄인다."""
    if not digits.isdigit():
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _mask_card_if_luhn(match: re.Match[str]) -> str:
    digits = re.sub(r"[^0-9]", "", match.group(0))
    if 13 <= len(digits) <= 19 and _luhn_ok(digits):
        return placeholder(KIND_CARD)
    return match.group(0)


# ------------------------------------------------------------------- 규칙 목록

BUILTIN_RULES: tuple[MaskRule, ...] = (
    # 1. DB 연결 문자열 — 호스트·계정까지 통째로 지운다.
    MaskRule(
        kind=KIND_DB_URI,
        pattern=re.compile(
            r"\b(?:jdbc:)?(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?|"
            r"mssql|sqlserver|oracle|db2|clickhouse|cassandra|cockroachdb|memcached)"
            r"://[^\s\"'<>\\]+",
            _I,
        ),
        replacement=placeholder(KIND_DB_URI),
    ),
    # 2. 그 밖의 URI 에 박힌 자격증명 (`https://user:pass@host`). 호스트는 남긴다.
    MaskRule(
        kind=KIND_URI_CREDENTIALS,
        pattern=re.compile(r"""\b([A-Za-z][A-Za-z0-9+.\-]*://)[^\s/:@"']+:[^\s/@"']+@"""),
        replacement=r"\1" + placeholder(KIND_URI_CREDENTIALS) + "@",
    ),
    # 3. PEM 개인키 블록.
    MaskRule(
        kind=KIND_API_KEY,
        pattern=re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
        ),
        replacement=placeholder(KIND_API_KEY),
    ),
    # 4. JWT (`eyJ...` 3 파트).
    MaskRule(
        kind=KIND_JWT,
        pattern=re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*"),
        replacement=placeholder(KIND_JWT),
    ),
    # 5. 쿠키 — 값 전체(세미콜론 구분 다중 쿠키 포함)를 지운다.
    MaskRule(
        kind=KIND_COOKIE,
        pattern=re.compile(rf"\b(set-cookie|cookie)({_SEP})({_LINE_VALUE})", _I),
        replacement=r"\1\2" + placeholder(KIND_COOKIE),
    ),
    # 6. 세션 식별자.
    MaskRule(
        kind=KIND_COOKIE,
        pattern=re.compile(
            rf"\b(jsessionid|phpsessid|session[-_]?id|sessionid|csrf[-_]?token|xsrf[-_]?token)"
            rf"({_SEP})({_VALUE})",
            _I,
        ),
        replacement=r"\1\2" + placeholder(KIND_COOKIE),
    ),
    # 7. Authorization 계열 헤더 — 스킴을 모르는 경우까지 값 전체를 지운다.
    MaskRule(
        kind=KIND_BEARER_TOKEN,
        pattern=re.compile(
            rf"\b(proxy-authorization|authorization|x-auth-token|x-access-token)"
            rf"({_SEP})([^\"'\r\n,;]+)",
            _I,
        ),
        replacement=r"\1\2" + placeholder(KIND_BEARER_TOKEN),
    ),
    # 8. `Bearer <token>` / `Basic <base64>` — 스킴 이름은 남겨 문맥을 잃지 않는다.
    MaskRule(
        kind=KIND_BEARER_TOKEN,
        pattern=re.compile(r"\b(bearer|basic|digest)\s+([A-Za-z0-9._\-+/=~]{8,})", _I),
        replacement=r"\1 " + placeholder(KIND_BEARER_TOKEN),
    ),
    # 9. API 키 — 키 이름이 명시된 형태.
    MaskRule(
        kind=KIND_API_KEY,
        pattern=re.compile(
            rf"\b(x-api-key|api[-_]?key|apikey|api[-_]?token|access[-_]?token|refresh[-_]?token|"
            rf"id[-_]?token|session[-_]?token|client[-_]?secret|secret[-_]?key|private[-_]?key|"
            rf"auth[-_]?token|credentials?)({_SEP})({_VALUE})",
            _I,
        ),
        replacement=r"\1\2" + placeholder(KIND_API_KEY),
    ),
    # 10. `token=` / `secret=` 처럼 흔한 이름은 값이 충분히 길 때만 지운다
    #     (`token: expired` 같은 평범한 메시지를 통째로 날리지 않기 위해).
    MaskRule(
        kind=KIND_API_KEY,
        pattern=re.compile(
            rf"\b(token|secret)({_SEP})([A-Za-z0-9._\-+/=]{{12,}})",
            _I,
        ),
        replacement=r"\1\2" + placeholder(KIND_API_KEY),
    ),
    # 11. 프로바이더별 고정 접두사 키 (키 이름 없이 본문에 노출된 경우).
    MaskRule(
        kind=KIND_API_KEY,
        pattern=re.compile(
            r"\b(?:"
            r"sk-(?:ant-|proj-|live-|test-)?[A-Za-z0-9_\-]{16,}"
            r"|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}"
            r"|github_pat_[A-Za-z0-9_]{20,}"
            r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
            r"|glpat-[A-Za-z0-9_\-]{16,}"
            r"|AKIA[0-9A-Z]{16}"
            r"|ASIA[0-9A-Z]{16}"
            r"|AIza[0-9A-Za-z_\-]{35}"
            r")"
        ),
        replacement=placeholder(KIND_API_KEY),
    ),
    # 12. 비밀번호.
    MaskRule(
        kind=KIND_PASSWORD,
        pattern=re.compile(
            rf"\b(passphrase|pass[-_]?phrase|password|passwd|pwd|pass)({_SEP})({_VALUE})",
            _I,
        ),
        replacement=r"\1\2" + placeholder(KIND_PASSWORD),
    ),
    # 13. 이메일.
    MaskRule(
        kind=KIND_EMAIL,
        pattern=re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
        replacement=placeholder(KIND_EMAIL),
    ),
    # 14. 카드번호 — 4-4-4-4 형태는 무조건, 붙여 쓴 13~19 자리는 Luhn 통과 시.
    MaskRule(
        kind=KIND_CARD,
        pattern=re.compile(r"\b\d{4}[ \-]\d{4}[ \-]\d{4}[ \-]\d{4}\b"),
        replacement=placeholder(KIND_CARD),
    ),
    MaskRule(
        kind=KIND_CARD,
        pattern=re.compile(r"\b(?:\d[ \-]?){12,18}\d\b"),
        replacement=_mask_card_if_luhn,
    ),
    # 15. 전화번호 (국내 `010-1234-5678`, 국가번호 포함, E.164).
    MaskRule(
        kind=KIND_PHONE,
        pattern=re.compile(
            r"(?:\+\d{1,3}[ \-]?)?(?:\(\d{2,4}\)|\d{2,4})[ \-]\d{3,4}[ \-]\d{4}\b"
        ),
        replacement=placeholder(KIND_PHONE),
    ),
    MaskRule(
        kind=KIND_PHONE,
        pattern=re.compile(r"\+\d{10,14}\b"),
        replacement=placeholder(KIND_PHONE),
    ),
)


__all__ = [
    "BUILTIN_RULES",
    "KINDS",
    "KIND_API_KEY",
    "KIND_BEARER_TOKEN",
    "KIND_CARD",
    "KIND_COOKIE",
    "KIND_CUSTOM",
    "KIND_DB_URI",
    "KIND_EMAIL",
    "KIND_JWT",
    "KIND_PASSWORD",
    "KIND_PHONE",
    "KIND_URI_CREDENTIALS",
    "PLACEHOLDER_FORMAT",
    "PLACEHOLDER_RE",
    "MaskRule",
    "placeholder",
]
