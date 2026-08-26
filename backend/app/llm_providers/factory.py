"""`LLMConnection` ORM 행 -> `LLMProvider` 인스턴스.

LLM 호출 경로를 한 곳으로 모으기 위해, 다른 트랙(분석)은 어댑터를 직접 만들지 말고
**반드시 이 팩토리**를 통한다. API 키 복호화 책임도 여기에 있다 (`LLMProvider`
구현체는 이미 복호화된 평문을 받는다).

타임아웃은 프로바이더 무관 비용 통제이므로 SDK 기본값이 아니라
`app.config.Settings.llm_timeout_seconds` 를 쓴다.
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.crypto import decrypt
from app.enums import LLMProviderName
from app.llm_providers.anthropic_provider import AnthropicProvider
from app.llm_providers.openai_provider import OpenAIProvider
from app.providers.llm import LLMProvider

#: 지원 프로바이더. 그 외 값은 ValueError 다 — 조용히 대체하면 잘못된 모델로 과금된다.
#: `openai_compatible` 은 OpenAI 어댑터로 매핑된다 (base_url 필수).
SUPPORTED_PROVIDERS: tuple[str, ...] = (
    LLMProviderName.OPENAI.value,
    LLMProviderName.OPENAI_COMPATIBLE.value,
    LLMProviderName.ANTHROPIC.value,
)


def build_llm_provider(connection: Any) -> LLMProvider:
    """`LLMConnection` 을 받아 어댑터를 만든다.

    `encrypted_api_key` 는 `app.crypto.decrypt` 로 복호화해 넘긴다.
    `provider` 가 `openai` / `anthropic` 이 아니면 `ValueError` 를 던진다.
    """
    if connection is None:
        raise ValueError("connection 이 None 입니다.")

    encrypted_api_key = getattr(connection, "encrypted_api_key", None)
    api_key = decrypt(encrypted_api_key) if encrypted_api_key else None

    return build_llm_provider_from_values(
        provider=str(getattr(connection, "provider", "") or ""),
        model=str(getattr(connection, "model", "") or ""),
        api_key=api_key,
        base_url=getattr(connection, "base_url", None),
    )


def build_llm_provider_from_values(
    *,
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
) -> LLMProvider:
    """저장 전 입력값(연결 테스트 등)으로 어댑터를 만든다. 복호화는 호출자 몫이다."""
    provider_name = str(provider or "")
    if timeout_seconds is None:
        timeout_seconds = get_settings().llm_timeout_seconds

    if provider_name in (LLMProviderName.OPENAI.value, LLMProviderName.OPENAI_COMPATIBLE.value):
        if provider_name == LLMProviderName.OPENAI_COMPATIBLE.value and not base_url:
            raise ValueError("provider='openai_compatible' 은 base_url 이 필수입니다.")
        return OpenAIProvider(
            model=model,
            api_key=api_key,
            base_url=base_url or None,
            timeout_seconds=timeout_seconds,
        )
    if provider_name == LLMProviderName.ANTHROPIC.value:
        return AnthropicProvider(
            model=model,
            api_key=api_key,
            base_url=base_url or None,
            timeout_seconds=timeout_seconds,
        )

    raise ValueError(
        f"지원하지 않는 provider 입니다: {provider_name!r} "
        f"(지원: {', '.join(SUPPORTED_PROVIDERS)}). "
        "OpenAI 호환 엔드포인트는 provider='openai' 에 base_url 을 지정해 쓰세요."
    )


def list_models(
    *,
    provider: str,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
) -> list[str]:
    """프로바이더의 모델 목록. 지원 판정과 base_url 규칙을 `build_*` 와 공유한다.

    모델을 **고르기 전에** 부르는 조회라 어댑터 인스턴스(=model 필수)를 만들지 않고
    각 어댑터의 `list_models` classmethod 로 직접 간다. 토큰을 쓰지 않는 무과금
    호출이며, 실패는 어댑터가 `LLMError` 로 감싼다.
    """
    provider_name = str(provider or "")
    if timeout_seconds is None:
        timeout_seconds = get_settings().llm_timeout_seconds

    if provider_name in (LLMProviderName.OPENAI.value, LLMProviderName.OPENAI_COMPATIBLE.value):
        if provider_name == LLMProviderName.OPENAI_COMPATIBLE.value and not base_url:
            raise ValueError("provider='openai_compatible' 은 base_url 이 필수입니다.")
        return OpenAIProvider.list_models(
            api_key=api_key, base_url=base_url or None, timeout_seconds=timeout_seconds
        )
    if provider_name == LLMProviderName.ANTHROPIC.value:
        return AnthropicProvider.list_models(
            api_key=api_key, base_url=base_url or None, timeout_seconds=timeout_seconds
        )

    raise ValueError(
        f"지원하지 않는 provider 입니다: {provider_name!r} "
        f"(지원: {', '.join(SUPPORTED_PROVIDERS)})."
    )


__all__ = [
    "SUPPORTED_PROVIDERS",
    "build_llm_provider",
    "build_llm_provider_from_values",
    "list_models",
]
