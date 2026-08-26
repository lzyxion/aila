"""프로바이더 모델 목록 조회 (`list_models`).

Phase 4 — 1 차 사용 피드백: 모델명을 **외워서 타이핑**해야 했다.

실제 API 는 절대 호출하지 않는다 — SDK 클라이언트를 통째로 대체한다.
여기서 고정하는 계약은 다섯이다.

1. `list_models` 는 **classmethod** 다. 모델을 고르기 전에 부르는 조회라
   `model` 이 필수인 생성자를 태울 수 없다 (어댑터 ABC 는 그대로 둔다).
2. 목록 조회는 토큰을 쓰지 않는 **무과금** 호출이다 — 채팅/메시지 API 를 부르지 않는다.
3. 응답에서 뽑는 것은 id 문자열뿐이고, **순서는 프로바이더가 준 그대로** 둔다.
4. SDK 예외는 전부 `LLMError` 로 감싼다 (status_code 보존).
5. provider 분기·`openai_compatible` 의 base_url 필수 규칙은 `build_*` 와 **같은**
   판정을 쓴다 (factory 한 곳).
"""

from __future__ import annotations

from typing import Any

import pytest
from anthropic import AnthropicError
from openai import OpenAIError

from app.llm_providers import factory
from app.llm_providers.anthropic_provider import MODEL_LIST_LIMIT, AnthropicProvider
from app.llm_providers.openai_provider import OpenAIProvider
from app.providers.llm import LLMError


class FakeModel:
    def __init__(self, model_id: Any) -> None:
        self.id = model_id


class FakePage:
    """SDK 의 `SyncPage` 대역 — `.data` 를 갖고 순회도 된다."""

    def __init__(self, models: list[Any]) -> None:
        self.data = models

    def __iter__(self):
        return iter(self.data)


class FakeModels:
    def __init__(self, page: Any = None, error: Exception | None = None) -> None:
        self.page = page
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.page


class FakeClient:
    def __init__(self, page: Any = None, error: Exception | None = None) -> None:
        self.models = FakeModels(page, error)
        # 과금 경로를 건드리면 즉시 터지도록 둔다.
        self.chat = _Forbidden("chat.completions")
        self.messages = _Forbidden("messages")


class _Forbidden:
    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, item: str) -> Any:
        raise AssertionError(f"모델 목록 조회가 과금 경로({self._name})를 호출했습니다.")


class FakeOpenAIError(OpenAIError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeAnthropicError(AnthropicError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.fixture
def install_openai(monkeypatch: pytest.MonkeyPatch):
    created: dict[str, Any] = {}

    def _install(page: Any = None, error: Exception | None = None) -> FakeClient:
        client = FakeClient(page, error)

        def _factory(**kwargs: Any) -> FakeClient:
            created["kwargs"] = kwargs
            return client

        monkeypatch.setattr("app.llm_providers.openai_provider.OpenAI", _factory)
        client.created = created  # type: ignore[attr-defined]
        return client

    return _install


@pytest.fixture
def install_anthropic(monkeypatch: pytest.MonkeyPatch):
    created: dict[str, Any] = {}

    def _install(page: Any = None, error: Exception | None = None) -> FakeClient:
        client = FakeClient(page, error)

        def _factory(**kwargs: Any) -> FakeClient:
            created["kwargs"] = kwargs
            return client

        monkeypatch.setattr("app.llm_providers.anthropic_provider.Anthropic", _factory)
        client.created = created  # type: ignore[attr-defined]
        return client

    return _install


# --------------------------------------------------------------- OpenAI


def test_openai_list_models_returns_ids_in_provider_order(install_openai) -> None:
    install_openai(FakePage([FakeModel("gpt-4o-mini"), FakeModel("gpt-4o"), FakeModel("o3")]))

    assert OpenAIProvider.list_models(api_key="sk-plain") == ["gpt-4o-mini", "gpt-4o", "o3"]


def test_openai_list_models_needs_no_model_argument() -> None:
    """생성자와 달리 model 을 요구하지 않는다 (모델을 고르기 전에 부르는 조회)."""
    with pytest.raises(ValueError):
        OpenAIProvider(model="")
    assert callable(OpenAIProvider.list_models)


def test_openai_list_models_does_not_call_a_billed_endpoint(install_openai) -> None:
    client = install_openai(FakePage([FakeModel("gpt-4o")]))

    OpenAIProvider.list_models(api_key="sk-plain")

    # models.list() 만 불렀고 인자도 없다 (토큰 소비 없음).
    assert client.models.calls == [{}]


def test_openai_list_models_accepts_a_page_without_data(install_openai) -> None:
    install_openai([FakeModel("gpt-4o"), {"id": "gpt-4o-mini"}])

    assert OpenAIProvider.list_models(api_key="sk-plain") == ["gpt-4o", "gpt-4o-mini"]


def test_openai_list_models_drops_junk_entries_and_duplicates(install_openai) -> None:
    install_openai(
        FakePage(
            [
                FakeModel("gpt-4o"),
                FakeModel(None),
                FakeModel(""),
                FakeModel(7),
                object(),
                FakeModel("gpt-4o"),
                FakeModel("o3"),
            ]
        )
    )

    assert OpenAIProvider.list_models(api_key="sk-plain") == ["gpt-4o", "o3"]


def test_openai_list_models_passes_key_base_url_and_timeout(install_openai) -> None:
    client = install_openai(FakePage([]))

    OpenAIProvider.list_models(
        api_key="sk-plain", base_url="https://gateway.example/v1", timeout_seconds=7.0
    )

    assert client.created["kwargs"] == {
        "timeout": 7.0,
        "api_key": "sk-plain",
        "base_url": "https://gateway.example/v1",
    }


def test_openai_list_models_allows_keyless_compatible_endpoint(install_openai) -> None:
    client = install_openai(FakePage([FakeModel("llm-mock-1")]))

    assert OpenAIProvider.list_models(base_url="http://llm-mock:8000/v1") == ["llm-mock-1"]
    assert client.created["kwargs"]["api_key"] == "not-required"


def test_openai_sdk_error_becomes_llm_error_with_status(install_openai) -> None:
    install_openai(error=FakeOpenAIError("invalid api key", status_code=401))

    with pytest.raises(LLMError) as excinfo:
        OpenAIProvider.list_models(api_key="sk-bad")

    assert excinfo.value.status_code == 401
    assert "invalid api key" in str(excinfo.value)


# ------------------------------------------------------------ Anthropic


def test_anthropic_list_models_returns_ids_in_provider_order(install_anthropic) -> None:
    install_anthropic(FakePage([FakeModel("claude-newest"), FakeModel("claude-older")]))

    assert AnthropicProvider.list_models(api_key="sk-ant") == [
        "claude-newest",
        "claude-older",
    ]


def test_anthropic_list_models_asks_for_a_full_page(install_anthropic) -> None:
    """기본 페이지 크기가 작아 최신 몇 개만 보이는 일이 없어야 한다."""
    client = install_anthropic(FakePage([FakeModel("claude-newest")]))

    AnthropicProvider.list_models(api_key="sk-ant")

    assert client.models.calls == [{"limit": MODEL_LIST_LIMIT}]


def test_anthropic_list_models_falls_back_when_sdk_lacks_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class OldModels:
        def list(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            if kwargs:
                raise TypeError("list() got an unexpected keyword argument 'limit'")
            return FakePage([FakeModel("claude-older")])

    class OldClient:
        def __init__(self) -> None:
            self.models = OldModels()

    monkeypatch.setattr(
        "app.llm_providers.anthropic_provider.Anthropic", lambda **kwargs: OldClient()
    )

    assert AnthropicProvider.list_models(api_key="sk-ant") == ["claude-older"]
    assert calls == [{"limit": MODEL_LIST_LIMIT}, {}]


def test_anthropic_list_models_passes_key_base_url_and_timeout(install_anthropic) -> None:
    client = install_anthropic(FakePage([]))

    AnthropicProvider.list_models(
        api_key="sk-ant", base_url="https://proxy.example", timeout_seconds=9.0
    )

    assert client.created["kwargs"] == {
        "timeout": 9.0,
        "api_key": "sk-ant",
        "base_url": "https://proxy.example",
    }


def test_anthropic_sdk_error_becomes_llm_error_with_status(install_anthropic) -> None:
    install_anthropic(error=FakeAnthropicError("overloaded", status_code=529))

    with pytest.raises(LLMError) as excinfo:
        AnthropicProvider.list_models(api_key="sk-ant")

    assert excinfo.value.status_code == 529
    assert "overloaded" in str(excinfo.value)


# --------------------------------------------------------------- factory


def test_factory_dispatches_openai(install_openai) -> None:
    install_openai(FakePage([FakeModel("gpt-4o")]))

    assert factory.list_models(provider="openai", api_key="sk-plain") == ["gpt-4o"]


def test_factory_dispatches_anthropic(install_anthropic) -> None:
    install_anthropic(FakePage([FakeModel("claude-newest")]))

    assert factory.list_models(provider="anthropic", api_key="sk-ant") == ["claude-newest"]


def test_factory_openai_compatible_uses_the_openai_path(install_openai) -> None:
    client = install_openai(FakePage([FakeModel("llm-mock-1")]))

    models = factory.list_models(
        provider="openai_compatible", base_url="http://llm-mock:8000/v1"
    )

    assert models == ["llm-mock-1"]
    assert client.created["kwargs"]["base_url"] == "http://llm-mock:8000/v1"


def test_factory_openai_compatible_requires_base_url() -> None:
    """`build_llm_provider_from_values` 와 **같은** 규칙이어야 한다."""
    with pytest.raises(ValueError, match="base_url"):
        factory.list_models(provider="openai_compatible", api_key="sk-plain")


@pytest.mark.parametrize("provider", ["", "gemini", "OpenAI "])
def test_factory_rejects_unsupported_providers(provider: str) -> None:
    with pytest.raises(ValueError, match="지원하지 않는 provider"):
        factory.list_models(provider=provider)
