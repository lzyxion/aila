"""도메인 열거형. 모델과 API 스키마가 같은 값을 쓰도록 한곳에 둔다.

DB 에는 네이티브 ENUM 이 아니라 문자열로 저장한다 — 값 추가에 마이그레이션이
필요 없게 하기 위해서다.
"""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    """로그 소스 종류. MVP 는 `loki` 하나만 구현한다."""

    LOKI = "loki"


class AuthType(StrEnum):
    """로그 소스 인증 방식."""

    NONE = "none"
    BASIC = "basic"
    BEARER = "bearer"
    HEADER = "header"


class LLMProviderName(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"


class UserRole(StrEnum):
    """계정 권한. `viewer` 는 GET 만, `admin` 은 전부 허용한다 (Phase 5)."""

    ADMIN = "admin"
    VIEWER = "viewer"


class TriggeredBy(StrEnum):
    """실행을 시작한 주체. 이력·화면 배지용이며 동작을 분기하지 않는다 (Phase 5)."""

    MANUAL = "manual"
    SCHEDULE = "schedule"


class QueryRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AnalysisJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class UsageStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Severity(StrEnum):
    """LLM 이 추정한 심각도. 발생량 기반 지표가 아니다 — 화면에서 분리해 표시한다."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


#: 진행 중으로 간주하는 분석 작업 상태 (멱등 처리에서 기존 작업 반환 대상)
ACTIVE_JOB_STATUSES: frozenset[AnalysisJobStatus] = frozenset(
    {AnalysisJobStatus.PENDING, AnalysisJobStatus.RUNNING}
)
