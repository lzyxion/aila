"""`build_llm_provider()` 팩토리 테스트 — 분기와 복호화.

트랙 간 계약: 분석 트랙은 어댑터를 직접 만들지 않고 이 팩토리만 쓴다.
저장된 `encrypted_api_key` 복호화 책임도 여기에 있다 (어댑터는 평문을 받는다).
"""

from __future__ import annotations

import pytest

from app.crypto import DecryptionError, encrypt
from app.llm_providers import build_llm_provider, build_llm_provider_from_values
from app.llm_providers.anthropic_provider import AnthropicProvider
from app.llm_providers.openai_provider import OpenAIProvider
from app.models import LLMConnection
from app.providers.llm import LLMProvider


def _connection(**overrides) -> LLMConnection:
    fields = {
        "name": "llm-1",
        "provider": "openai",
        "model": "gpt-test",
        "base_url": None,
        "encrypted_api_key": encrypt("sk-plain"),
        "is_default": True,
        "active": True,
    }
    fields.update(overrides)
    return LLMConnection(**fields)


def test_openai_connection_builds_openai_provider() -> None:
    provider = build_llm_provider(_connection())

    assert isinstance(provider, OpenAIProvider)
    assert isinstance(provider, LLMProvider)
    assert provider.model == "gpt-test"
    # 저장된 암호문이 아니라 복호화된 평문이 어댑터로 들어간다.
    assert provider.api_key == "sk-plain"


def test_anthropic_connection_builds_anthropic_provider() -> None:
    provider = build_llm_provider(
        _connection(provider="anthropic", model="claude-test", encrypted_api_key=encrypt("sk-ant"))
    )

    assert isinstance(provider, AnthropicProvider)
    assert provider.api_key == "sk-ant"


def test_base_url_is_passed_to_compatible_endpoint() -> None:
    provider = build_llm_provider(_connection(base_url="http://vllm.internal:8000/v1"))

    assert provider.base_url == "http://vllm.internal:8000/v1"


def test_missing_api_key_is_allowed() -> None:
    provider = build_llm_provider(_connection(encrypted_api_key=None))

    assert provider.api_key is None


def test_unsupported_provider_raises_value_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        build_llm_provider(_connection(provider="gemini"))

    assert "gemini" in str(excinfo.value)


def test_openai_compatible_enum_value_is_not_supported_directly() -> None:
    """계약: 어댑터는 openai / anthropic 둘뿐이다.

    `LLMProviderName.OPENAI_COMPATIBLE` 은 provider='openai' + base_url 로 쓴다.
    """
    with pytest.raises(ValueError):
        build_llm_provider(_connection(provider="openai_compatible"))


def test_none_connection_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_llm_provider(None)


def test_corrupted_api_key_raises_decryption_error() -> None:
    with pytest.raises(DecryptionError):
        build_llm_provider(_connection(encrypted_api_key="not-a-fernet-token"))


def test_from_values_does_not_decrypt() -> None:
    provider = build_llm_provider_from_values(
        provider="openai", model="gpt-test", api_key="plain", base_url=None
    )

    assert isinstance(provider, OpenAIProvider)
    assert provider.api_key == "plain"


def test_from_values_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        build_llm_provider_from_values(provider="", model="gpt-test")
