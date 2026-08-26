"""테스트 공통 설정.

계약 테스트는 **DB 없이** 돌아야 한다. 여기서 SQLite URL 과 테스트용 암호화 키를
`app` 모듈 import 전에 주입한다.

Phase 5 부터 `/api/**` 는 전부 인증이 필요하다. 기존 테스트 400 여 개는 전부
"로그인한 관리자" 를 전제로 쓰인 것이므로, 여기서 **기본값으로 admin 세션**을
깔아 준다 (`_default_admin_session`). 인증·권한 그 자체를 검증하는 테스트는
`@pytest.mark.real_auth` 를 붙여 이 기본값에서 빠져나온다.

대체 지점을 `auth.dependencies.identity_from_request` 하나로 고른 이유:
- 라우터·서비스 코드는 손대지 않는다 (테스트 전용 우회 경로를 앱에 심지 않는다).
- 앱 인스턴스를 어디서 만들든 적용된다 — 테스트 모듈 4 곳이 각자 `create_app()`
  을 부르므로 `dependency_overrides` 방식으로는 4 곳을 다 고쳐야 한다.
- 401/403 판정 로직(`enforce_api_auth`)은 **그대로 돈다**. 바뀌는 것은 "이 요청의
  주체가 누구인가" 뿐이라, 권한 규칙이 깨지면 인증 테스트가 잡아낸다.
"""

from __future__ import annotations

import os

os.environ.setdefault("AILA_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("AILA_CORS_ORIGINS", "http://localhost:5173")
# 백그라운드 스케줄러는 lifespan 에서 뜬다. 테스트는 tick 을 직접 부르므로 끈다
# (켜 두면 테스트 프로세스가 전역 세션 팩토리로 실제 정책 실행을 시도한다).
os.environ.setdefault("AILA_SCHEDULER_ENABLED", "false")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("AILA_ENCRYPTION_KEY", Fernet.generate_key().decode())

from collections.abc import Iterator  # noqa: E402
from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402

from app.auth.service import Identity  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.enums import UserRole  # noqa: E402

#: 기본 테스트 클라이언트가 들고 다니는 주체.
TEST_ADMIN = Identity(user_id=1, username="test-admin", role=UserRole.ADMIN.value)


@pytest.fixture(scope="session", autouse=True)
def _settings_cache_cleared():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _default_admin_session(request: pytest.FixtureRequest) -> Iterator[None]:
    """모든 테스트 클라이언트에 admin 세션을 기본 제공한다.

    `@pytest.mark.real_auth` 가 붙은 테스트는 제외 — 쿠키·401·403·만료를 실제
    경로로 검증해야 하기 때문이다.
    """
    if request.node.get_closest_marker("real_auth") is not None:
        yield
        return
    with patch("app.auth.dependencies.identity_from_request", return_value=TEST_ADMIN):
        yield
