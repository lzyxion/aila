"""민감정보 마스킹 진입점.

트랙 간 계약:
- `MASKING_RULE_VERSION: str = "v1"`
- `def mask(text: str, extra_patterns: Sequence[str] = ()) -> str`

성질 세 가지를 지킨다.

1. **순수 함수.** DB·네트워크·전역 상태를 보지 않는다.
2. **멱등.** 설계상 마스킹은 화면 표시 전과 LLM 전송 직전에 두 번 걸리므로
   `mask(mask(x)) == mask(x)` 여야 한다.
3. **실패는 조용히 넘어가지 않는다.** 사용자 정의 정규식이 잘못되면
   `MaskingPatternError` 를 던진다 — 마스킹 규칙이 조용히 빠지는 것이
   이 모듈에서 유일하게 되돌릴 수 없는 사고다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

from app.masking.rules import (
    BUILTIN_RULES,
    KIND_CUSTOM,
    KINDS,
    PLACEHOLDER_RE,
    MaskRule,
    placeholder,
)

#: 마스킹 규칙 버전. 규칙을 고치면 반드시 올린다 (`error_samples.masking_rule_version`).
#:
#: - v1: 최초 규칙 집합.
#: - v2: Authorization·Cookie 규칙의 값 범위를 줄 끝에서 **공백 경계**로 좁혀
#:   `<MASKED:...>` 뒤 문맥(`status=`, 예외명, 뒤따르는 key=value)을 보존한다.
#:   비밀값 자체는 그대로 지운다 — 약화가 아니라 과잉만 줄인 변경이다.
MASKING_RULE_VERSION: str = "v2"


class MaskingPatternError(ValueError):
    """사용자 정의 정규식이 컴파일되지 않을 때. 정책 저장 시점에 미리 검증하는 게 좋다."""


@lru_cache(maxsize=256)
def _compile_extra(patterns: tuple[str, ...]) -> tuple[MaskRule, ...]:
    rules: list[MaskRule] = []
    for raw in patterns:
        if not raw:
            continue
        try:
            compiled = re.compile(raw)
        except re.error as exc:  # 잘못된 규칙을 조용히 무시하지 않는다.
            raise MaskingPatternError(f"잘못된 마스킹 정규식입니다: {raw!r} ({exc})") from exc
        rules.append(
            MaskRule(kind=KIND_CUSTOM, pattern=compiled, replacement=placeholder(KIND_CUSTOM))
        )
    return tuple(rules)


def compile_extra_patterns(extra_patterns: Sequence[str]) -> tuple[MaskRule, ...]:
    """사용자 정의 정규식을 미리 검증한다 (정책 저장 시 사용).

    잘못된 패턴이 있으면 `MaskingPatternError` 를 던진다.
    """
    return _compile_extra(tuple(extra_patterns))


def mask(text: str, extra_patterns: Sequence[str] = ()) -> str:
    """민감정보를 `<MASKED:종류>` 플레이스홀더로 치환한다.

    사용자 정의 규칙을 **먼저** 적용한다 — 조직 고유 규칙이 내장 규칙보다 우선한다.
    """
    if not text:
        return text

    masked = text
    for rule in compile_extra_patterns(extra_patterns):
        masked = rule.apply(masked)
    for rule in BUILTIN_RULES:
        masked = rule.apply(masked)
    return masked


def mask_mapping(
    values: dict[str, str], extra_patterns: Sequence[str] = ()
) -> dict[str, str]:
    """라벨처럼 값이 문자열인 매핑을 통째로 마스킹한다 (키는 건드리지 않는다)."""
    return {key: mask(value, extra_patterns) for key, value in values.items()}


def contains_placeholder(text: str) -> bool:
    """마스킹이 실제로 무언가를 지웠는지 (테스트·감사용)."""
    return bool(PLACEHOLDER_RE.search(text or ""))


__all__ = [
    "KINDS",
    "MASKING_RULE_VERSION",
    "MaskingPatternError",
    "compile_extra_patterns",
    "contains_placeholder",
    "mask",
    "mask_mapping",
    "placeholder",
]
