"""다른 트랙이 구현하는 계약을 **지연 import** 로 감싸는 얇은 층.

Phase 2 담당 트랙: **분석·usage·보고서** (소비자 측)

`app.llm_providers.factory.build_llm_provider` 는 어댑터 트랙이 **동시에** 만들고 있고,
`app.masking.service.mask` 는 Phase 1 산출물이다. 분석 트랙이 모듈 최상단에서 그것들을
import 하면 파일이 아직 없는 동안 `app` 패키지 전체의 import 가 깨진다
(계약 테스트 `test_all_app_modules_import`).

그래서 여기서 하는 일은 두 가지뿐이다 (`app.policies.integrations` 와 같은 패턴).

1. **호출 시점에** import 한다 — 파일이 생기기 전에도 모듈 import 는 성공한다.
2. 분석 트랙 테스트의 **단일 mock 지점**이 된다 —
   `unittest.mock.patch("app.analysis.integrations.build_llm_provider")` 로
   상대 트랙 구현·실제 LLM 호출 없이 실행 흐름 전체를 검증한다.

호출하는 쪽은 반드시 `integrations.build_llm_provider(...)` 처럼 **모듈 속성으로**
접근한다. `from ... import build_llm_provider` 로 이름을 미리 묶으면 patch 가 먹지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 타입 체크 전용
    from app.providers.llm import LLMProvider

#: 상대 트랙 모듈이 아직 없을 때 쓰는 기본 규칙 버전 (모델 컬럼 기본값과 같다).
DEFAULT_MASKING_RULE_VERSION = "v1"


def build_llm_provider(connection: Any) -> LLMProvider:
    """`app.llm_providers.factory.build_llm_provider` — 연결 ORM 객체 → LLM 어댑터.

    API 키 복호화(`app.crypto.decrypt`)는 팩토리 안에서 한다. 분석 트랙은 복호화된
    값을 만지지 않는다 (평문 키가 프롬프트 조립 경로에 들어오지 않게 하기 위해서다).
    """
    try:
        from app.llm_providers.factory import build_llm_provider as _build_llm_provider
    except ImportError:  # 어댑터 트랙이 패키지 __init__ 로만 노출한 경우까지 받아준다.
        from app.llm_providers import build_llm_provider as _build_llm_provider

    return _build_llm_provider(connection)


def mask(text: str, extra_patterns: Sequence[str] = ()) -> str:
    """`app.masking.service.mask` — **LLM 전송 직전** 마스킹.

    마스킹은 멱등이므로(`mask(mask(x)) == mask(x)`) 이미 마스킹된 샘플에 한 번 더
    걸어도 결과가 망가지지 않는다. 조립 과정에서 섞여 들어온 값(라벨·정규화 메시지·
    스택 프레임)까지 한 번에 덮는 것이 이 두 번째 호출의 목적이다.
    """
    from app.masking.service import mask as _mask

    return _mask(text, extra_patterns)


def masking_rule_version() -> str:
    """`app.masking.service.MASKING_RULE_VERSION` (없으면 기본값)."""
    try:
        from app.masking.service import MASKING_RULE_VERSION
    except ImportError:  # pragma: no cover - 마스킹 트랙 미구현 시 방어
        return DEFAULT_MASKING_RULE_VERSION
    return MASKING_RULE_VERSION


__all__ = [
    "DEFAULT_MASKING_RULE_VERSION",
    "build_llm_provider",
    "mask",
    "masking_rule_version",
]
