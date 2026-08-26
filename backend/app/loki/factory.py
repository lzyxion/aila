"""`LokiConnection` ORM 행 -> `LogSourceProvider` 인스턴스.

쿼리 실행 경로를 한 곳으로 모으기 위해, 다른 트랙(정책·대시보드)은 프로바이더를
직접 만들지 말고 **반드시 이 팩토리**를 통한다. secret 복호화 책임도 여기에 있다
(`LogSourceProvider` 구현체는 이미 복호화된 값을 받는다).

타임아웃은 소스 무관 비용 통제이므로 어댑터 기본값이 아니라
`app.config.Settings.query_timeout_seconds` 를 쓴다.
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.crypto import decrypt
from app.enums import SourceType
from app.loki.provider import LokiProvider
from app.providers.logsource import LogSourceProvider


def build_provider(connection: Any) -> LogSourceProvider:
    """`LokiConnection` 을 받아 `LokiProvider` 를 만든다.

    `source_type` 이 `loki` 가 아니면 `ValueError` 를 던진다 — MVP 에 두 번째
    어댑터는 없고, 조용히 Loki 로 대체하면 잘못된 소스를 조회하게 된다.
    """
    if connection is None:
        raise ValueError("connection 이 None 입니다.")

    source_type = str(getattr(connection, "source_type", "") or "")
    if source_type != SourceType.LOKI.value:
        raise ValueError(
            f"지원하지 않는 source_type 입니다: {source_type!r} "
            f"(MVP 는 {SourceType.LOKI.value!r} 만 구현한다)"
        )

    encrypted_secret = getattr(connection, "encrypted_secret", None)
    secret = decrypt(encrypted_secret) if encrypted_secret else None

    return LokiProvider(
        base_url=str(getattr(connection, "base_url", "") or ""),
        auth_type=str(getattr(connection, "auth_type", "none") or "none"),
        secret=secret,
        label_mapping=dict(getattr(connection, "label_mapping", None) or {}),
        timeout_seconds=get_settings().query_timeout_seconds,
    )


__all__ = ["build_provider"]
