"""`app.loki.factory.build_provider` 계약 테스트.

정책 API 트랙이 이 함수를 import 한다 — 시그니처와 예외 계약이 바뀌면 안 된다.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.crypto import encrypt
from app.loki.factory import build_provider
from app.loki.provider import LokiProvider
from app.models import LogSourceConnection


def make_connection(**overrides) -> LogSourceConnection:
    kwargs = {
        "name": "loki-local",
        "source_type": "loki",
        "base_url": "http://loki.test:3100/",
        "auth_type": "bearer",
        "encrypted_secret": encrypt("tok-123"),
        "label_mapping": {"service": "app"},
        "active": True,
    }
    kwargs.update(overrides)
    return LogSourceConnection(**kwargs)


def test_build_provider_decrypts_secret_and_uses_settings_timeout() -> None:
    provider = build_provider(make_connection())

    assert isinstance(provider, LokiProvider)
    assert provider.base_url == "http://loki.test:3100"  # 끝의 / 는 제거된다
    assert provider.auth_type == "bearer"
    assert provider.secret == "tok-123"  # 복호화 책임은 팩토리에 있다
    assert provider.label_mapping == {"service": "app"}
    assert provider.timeout_seconds == get_settings().query_timeout_seconds
    assert provider.source_type == "loki"


def test_build_provider_without_secret() -> None:
    provider = build_provider(make_connection(auth_type="none", encrypted_secret=None))
    assert isinstance(provider, LokiProvider)
    assert provider.secret is None


def test_build_provider_rejects_other_source_types() -> None:
    with pytest.raises(ValueError):
        build_provider(make_connection(source_type="elasticsearch"))


def test_build_provider_rejects_none() -> None:
    with pytest.raises(ValueError):
        build_provider(None)
