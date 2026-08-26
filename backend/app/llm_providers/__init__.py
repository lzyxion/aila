"""LLM 어댑터 패키지.

프로바이더별 구조화 출력 방식은 어댑터 안에 가둔다. 밖에서 쓰는 진입점은
`build_llm_provider(connection)` 하나다 (`app.providers.llm.LLMProvider` 반환).
"""

from __future__ import annotations

from app.llm_providers.anthropic_provider import AnthropicProvider
from app.llm_providers.factory import (
    SUPPORTED_PROVIDERS,
    build_llm_provider,
    build_llm_provider_from_values,
)
from app.llm_providers.openai_provider import OpenAIProvider
from app.llm_providers.schema import to_strict_json_schema

__all__ = [
    "SUPPORTED_PROVIDERS",
    "AnthropicProvider",
    "OpenAIProvider",
    "build_llm_provider",
    "build_llm_provider_from_values",
    "to_strict_json_schema",
]
