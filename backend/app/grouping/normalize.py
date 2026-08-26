"""가변값 제거(정규화) — fingerprint 앞 단계.

같은 버그가 요청 ID·타임스탬프·건수 때문에 다른 그룹으로 쪼개지지 않도록,
호출마다 달라지는 값을 `<종류>` 플레이스홀더로 바꾼다.

마스킹 플레이스홀더(`<MASKED:API_KEY>` 등)는 숫자·`@`·`.` 조합을 갖지 않으므로
여기서 다시 치환되지 않는다 — 마스킹 → 정규화 순서가 고정된 이유이기도 하다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizationRule:
    name: str
    pattern: re.Pattern[str]
    replacement: str | Callable[[re.Match[str]], str]

    def apply(self, text: str) -> str:
        return self.pattern.sub(self.replacement, text)


_I = re.IGNORECASE

#: `request_id=...` 처럼 이름이 붙은 상관관계 ID.
_ID_KEYS = (
    r"request[-_]?id|req[-_]?id|correlation[-_]?id|trace[-_]?id|span[-_]?id|transaction[-_]?id|"
    r"job[-_]?id|task[-_]?id|order[-_]?id|user[-_]?id|tenant[-_]?id|x-request-id|message[-_]?id"
)

RULES: tuple[NormalizationRule, ...] = (
    # UUID 는 숫자·16 진수 규칙보다 먼저 잡는다.
    NormalizationRule(
        "uuid",
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<UUID>",
    ),
    # ISO 8601 (날짜+시각). 날짜/시각 단독 규칙보다 먼저.
    NormalizationRule(
        "iso_timestamp",
        re.compile(
            r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
        ),
        "<TS>",
    ),
    NormalizationRule("date", re.compile(r"\b\d{4}[-/]\d{2}[-/]\d{2}\b"), "<DATE>"),
    NormalizationRule("time", re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<TIME>"),
    # 이름이 붙은 상관관계 ID (값 형태를 가리지 않는다).
    NormalizationRule(
        "named_id",
        re.compile(rf"""\b({_ID_KEYS})(["']?\s*[:=]\s*["']?)([^\s"',;&}}\)\]]+)""", _I),
        r"\1\2<ID>",
    ),
    # IP 주소 (포트 포함) / IPv6.
    NormalizationRule(
        "ipv4",
        re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b"),
        "<IP>",
    ),
    NormalizationRule(
        "ipv6",
        re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b"),
        "<IP>",
    ),
    # 16 진수 덩어리 (객체 주소, 해시, 요청 ID). 전부 숫자인 값은 <NUM> 쪽에 넘긴다.
    NormalizationRule(
        "hex",
        re.compile(r"\b(?:0x)?(?![0-9]+\b)[0-9a-fA-F]{8,}\b"),
        "<HEX>",
    ),
    # 남은 숫자 전부 (라인 번호, 건수, 소요 시간, 포트, 버전).
    # 뒤에 `\b` 를 두지 않는 이유는 `1200ms`·`3s` 처럼 단위가 붙은 값을 놓치기 때문이다.
    NormalizationRule("number", re.compile(r"(?<![\w.])\d+(?:\.\d+)*"), "<NUM>"),
    # 공백 정리 — 여러 줄 메시지도 한 줄로 눌러 fingerprint 안정성을 높인다.
    NormalizationRule("whitespace", re.compile(r"\s+"), " "),
)

#: 정규화가 만들어 내는 플레이스홀더 전체 (테스트·문서용).
PLACEHOLDERS: tuple[str, ...] = (
    "<UUID>",
    "<TS>",
    "<DATE>",
    "<TIME>",
    "<ID>",
    "<IP>",
    "<HEX>",
    "<NUM>",
)


def normalize(text: str | None) -> str:
    """가변값을 플레이스홀더로 치환하고 공백을 정리한다."""
    if not text:
        return ""
    normalized = text
    for rule in RULES:
        normalized = rule.apply(normalized)
    return normalized.strip()


__all__ = ["PLACEHOLDERS", "RULES", "NormalizationRule", "normalize"]
