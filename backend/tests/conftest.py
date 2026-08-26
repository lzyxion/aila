"""테스트 공통 설정.

계약 테스트는 **DB 없이** 돌아야 한다. 여기서 SQLite URL 과 테스트용 암호화 키를
`app` 모듈 import 전에 주입한다.
"""

from __future__ import annotations

import os

os.environ.setdefault("AILA_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("AILA_CORS_ORIGINS", "http://localhost:5173")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("AILA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _settings_cache_cleared():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
