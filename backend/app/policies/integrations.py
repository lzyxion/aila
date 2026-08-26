"""다른 트랙이 구현하는 계약을 **지연 import** 로 감싸는 얇은 층.

Phase 1 담당 트랙: **정책 API** (소비자 측)

`app.grouping.service` / `app.masking.service` / `app.loki.factory` 는 각각
그룹화·마스킹 트랙과 Loki 어댑터 트랙이 동시에 만들고 있다. 정책 API 트랙이
모듈 최상단에서 그것들을 import 하면 아직 파일이 없는 동안 `app` 패키지 전체의
import 가 깨진다 (계약 테스트 `test_all_app_modules_import`).

그래서 여기서 하는 일은 두 가지뿐이다.

1. **호출 시점에** import 한다 — 파일이 생기기 전에도 모듈 import 는 성공한다.
2. 정책 API 쪽 테스트의 **단일 mock 지점**이 된다 —
   `unittest.mock.patch("app.policies.integrations.group_records")` 처럼
   상대 트랙 구현 없이도 실행 흐름을 검증할 수 있다.

호출하는 쪽은 반드시 `integrations.group_records(...)` 처럼 **모듈 속성으로**
접근한다. `from ... import group_records` 로 이름을 미리 묶으면 patch 가 먹지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 타입 체크 전용
    from app.providers.logsource import LogSourceProvider
    from app.schemas.logrecord import LogRecord

#: 상대 트랙 모듈이 아직 없을 때 쓰는 기본 규칙 버전 (모델 컬럼 기본값과 같다).
DEFAULT_NORMALIZATION_RULE_VERSION = "v1"
DEFAULT_MASKING_RULE_VERSION = "v1"


def build_provider(connection: Any) -> LogSourceProvider:
    """`app.loki.factory.build_provider` — 연결 ORM 객체 → 로그 소스 어댑터.

    secret 복호화(`app.crypto.decrypt`)는 팩토리 안에서 한다. 정책 API 는 복호화된
    값을 만지지 않는다.
    """
    from app.loki.factory import build_provider as _build_provider

    return _build_provider(connection)


def group_records(
    records: list[LogRecord],
    *,
    max_samples_per_group: int,
    extra_mask_patterns: Sequence[str] = (),
) -> list[Any]:
    """`app.grouping.service.group_records` — 마스킹 → 정규화 → fingerprint.

    반환 원소는 `GroupedError` (fingerprint/service/environment/error_type/
    normalized_message/count/first_seen/last_seen/labels/top_stack_frame/samples).
    `samples[*].masked_log` 는 **이미 마스킹된 값**이다.
    """
    from app.grouping.service import group_records as _group_records

    return _group_records(
        records,
        max_samples_per_group=max_samples_per_group,
        extra_mask_patterns=extra_mask_patterns,
    )


def mask(text: str, extra_patterns: Sequence[str] = ()) -> str:
    """`app.masking.service.mask` — 화면 표시 전 마스킹."""
    from app.masking.service import mask as _mask

    return _mask(text, extra_patterns)


def normalization_rule_version() -> str:
    """`app.grouping.service.NORMALIZATION_RULE_VERSION` (없으면 기본값)."""
    try:
        from app.grouping.service import NORMALIZATION_RULE_VERSION
    except ImportError:
        return DEFAULT_NORMALIZATION_RULE_VERSION
    return NORMALIZATION_RULE_VERSION


def masking_rule_version() -> str:
    """`app.masking.service.MASKING_RULE_VERSION` (없으면 기본값)."""
    try:
        from app.masking.service import MASKING_RULE_VERSION
    except ImportError:
        return DEFAULT_MASKING_RULE_VERSION
    return MASKING_RULE_VERSION


__all__ = [
    "DEFAULT_MASKING_RULE_VERSION",
    "DEFAULT_NORMALIZATION_RULE_VERSION",
    "build_provider",
    "group_records",
    "mask",
    "masking_rule_version",
    "normalization_rule_version",
]
