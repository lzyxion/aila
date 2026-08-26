"""SQLAlchemy 2.x ORM 모델 (설계 문서 "데이터 모델 초안").

계약상 제약 두 가지를 여기서 강제한다.

1. **원본 로그는 저장하지 않는다.** `error_samples` 는 마스킹된 로그(`masked_log`)만
   보관한다. 마스킹 전 원문을 넣는 컬럼은 존재하지 않으며, 추가해서도 안 된다.
2. `error_groups` 는 `query_run_id` 에 매달려 있어 **조회 1 회 안에서만 유효**하다.
   조회 회차를 넘는 추적은 `fingerprint` 값 기준 조인으로만 한다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.enums import (
    AnalysisJobStatus,
    AuthType,
    QueryRunStatus,
    Severity,
    SourceType,
    UsageStatus,
)

#: PostgreSQL 에서는 JSONB, 그 외(SQLite 테스트)에서는 JSON.
JSONType = JSON().with_variant(JSONB(), "postgresql")

#: 대량 행 테이블용 PK 타입. SQLite 는 BIGINT AUTOINCREMENT 를 못 쓰므로 variant 를 둔다.
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------- connections


class LokiConnection(TimestampMixin, Base):
    """로그 소스 연결 정보. MVP 는 `source_type='loki'` 하나뿐이다."""

    __tablename__ = "loki_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SourceType.LOKI.value, server_default=SourceType.LOKI.value
    )
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    auth_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AuthType.NONE.value, server_default=AuthType.NONE.value
    )
    # app.crypto.encrypt() 결과만 저장한다. 평문 금지.
    encrypted_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 소스별 라벨 이름 차이를 어댑터가 흡수하기 위한 매핑.
    # 예: {"service": "app", "environment": "env", "level": "severity"}
    label_mapping: Mapped[dict] = mapped_column(
        JSONType, nullable=False, default=dict, server_default="{}"
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))

    policies: Mapped[list[AnalysisPolicy]] = relationship(back_populates="loki_connection")


class LLMConnection(TimestampMixin, Base):
    """LLM 연결 정보. `is_default=True` 인 행은 최대 하나만 유지한다(애플리케이션 규칙)."""

    __tablename__ = "llm_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # app.crypto.encrypt() 결과만 저장한다. API 응답에는 마스킹된 값만 싣는다.
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))

    __table_args__ = (Index("ix_llm_connections_is_default", "is_default"),)


# ---------------------------------------------------------------- policies


class AnalysisPolicy(TimestampMixin, Base):
    """분석 정책 = 쿼리문 + **실행 한도**. 한도 없이 쿼리만 저장하면 비용 통제가 불가능하다.

    쿼리문은 소스 고유 문법 그대로 저장한다(공통 DSL 로 번역하지 않는다).
    """

    __tablename__ = "analysis_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loki_connection_id: Mapped[int] = mapped_column(
        ForeignKey("loki_connections.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    logql: Mapped[str] = mapped_column(Text, nullable=False)

    # --- 한도 (UI 가 아니라 서버가 강제한다) ---
    default_range_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60"
    )
    max_lines: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1000, server_default="1000"
    )
    #: 제외 정규식 목록 (list[str])
    exclusions: Mapped[list] = mapped_column(
        JSONType, nullable=False, default=list, server_default="[]"
    )
    max_samples_per_group: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    allow_ai_analysis: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: 정책별 일일 분석 횟수 상한. NULL 이면 전역 한도(app_settings)만 적용한다.
    daily_analysis_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: DELETE 는 실제 삭제가 아니라 active=false 비활성화다.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))

    loki_connection: Mapped[LokiConnection] = relationship(back_populates="policies")
    query_runs: Mapped[list[QueryRun]] = relationship(back_populates="policy")

    __table_args__ = (
        UniqueConstraint("name", name="uq_analysis_policies_name"),
        CheckConstraint("max_lines > 0", name="ck_analysis_policies_max_lines"),
        CheckConstraint(
            "max_samples_per_group > 0", name="ck_analysis_policies_max_samples_per_group"
        ),
    )


class QueryRun(Base):
    """한 번의 로그 소스 조회 이력."""

    __tablename__ = "query_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_policies.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=QueryRunStatus.PENDING.value, server_default="pending"
    )
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: 어댑터가 표준화해 올린 메타데이터 (파싱 실패·한도 절단 등)
    dropped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    warnings: Mapped[list] = mapped_column(
        JSONType, nullable=False, default=list, server_default="[]"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    policy: Mapped[AnalysisPolicy] = relationship(back_populates="query_runs")
    error_groups: Mapped[list[ErrorGroup]] = relationship(
        back_populates="query_run", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_query_runs_policy_started", "policy_id", "started_at"),)


# ---------------------------------------------------------------- grouping


class ErrorGroup(Base):
    """그룹화 결과. 조회 1 회 범위 안에서만 유효하다.

    `fingerprint` 는 결정적이므로 조회 회차가 달라도 같은 오류는 같은 값을 갖는다.
    "이미 분석했는가"는 그룹 id 가 아니라 fingerprint 기준으로 판정한다.
    """

    __tablename__ = "error_groups"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    query_run_id: Mapped[int] = mapped_column(
        ForeignKey("query_runs.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    service: Mapped[str | None] = mapped_column(String(128), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    normalized_message: Mapped[str] = mapped_column(Text, nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: 대표 라벨 (원본 재조회용 — 원문을 저장하지 않으므로 이 라벨·시각으로 Loki 에서 다시 읽는다)
    labels: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict, server_default="{}")
    top_stack_frame: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 정규화 규칙 버전. 규칙을 고친 뒤 그룹 구성이 달라졌을 때 원인을 추적한다.
    normalization_rule_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="v1", server_default="v1"
    )

    query_run: Mapped[QueryRun] = relationship(back_populates="error_groups")
    samples: Mapped[list[ErrorSample]] = relationship(
        back_populates="error_group", cascade="all, delete-orphan"
    )
    analysis_jobs: Mapped[list[AnalysisJob]] = relationship(back_populates="error_group")

    __table_args__ = (
        UniqueConstraint("query_run_id", "fingerprint", name="uq_error_groups_run_fingerprint"),
        Index("ix_error_groups_fingerprint", "fingerprint"),
        Index("ix_error_groups_service_last_seen", "service", "last_seen"),
    )


class ErrorSample(Base):
    """마스킹된 대표 로그 샘플.

    **원본(마스킹 전) 로그는 저장하지 않는다.** 보존 기간(app_settings)이 지난 샘플은
    삭제한다 — 마스킹 규칙 강화는 이미 저장된 샘플에 소급되지 않기 때문이다.
    """

    __tablename__ = "error_samples"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    error_group_id: Mapped[int] = mapped_column(
        ForeignKey("error_groups.id", ondelete="CASCADE"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    masked_log: Mapped[str] = mapped_column(Text, nullable=False)
    labels: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict, server_default="{}")
    stacktrace: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 이 샘플에 적용된 마스킹 규칙 버전.
    masking_rule_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="v1", server_default="v1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    error_group: Mapped[ErrorGroup] = relationship(back_populates="samples")

    __table_args__ = (
        Index("ix_error_samples_group_occurred", "error_group_id", "occurred_at"),
        Index("ix_error_samples_created_at", "created_at"),
    )


# ---------------------------------------------------------------- analysis


class AnalysisJob(Base):
    """LLM 분석 작업 (POST 로 생성 → GET 폴링).

    `provider`·`model` 은 `llm_connection_id` 가 있어도 **값으로 중복 저장**한다.
    연결 설정의 모델을 나중에 바꿔도 과거 이력은 실제 사용한 모델을 유지해야 한다.
    """

    __tablename__ = "analysis_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    error_group_id: Mapped[int] = mapped_column(
        ForeignKey("error_groups.id", ondelete="CASCADE"), nullable=False
    )
    #: 연결이 지워져도 이력은 남아야 하므로 nullable + SET NULL.
    llm_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_connections.id", ondelete="SET NULL"), nullable=True
    )
    #: 조회 회차를 넘는 조회를 위해 그룹의 fingerprint 를 값으로 복사해 둔다.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AnalysisJobStatus.PENDING.value, server_default="pending"
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, server_default="v1")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    error_group: Mapped[ErrorGroup] = relationship(back_populates="analysis_jobs")
    result: Mapped[AnalysisResult | None] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )
    usage: Mapped[AnalysisUsageRecord | None] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        Index("ix_analysis_jobs_group_status", "error_group_id", "status"),
        Index("ix_analysis_jobs_fingerprint", "fingerprint"),
        Index("ix_analysis_jobs_requested_at", "requested_at"),
    )


class AnalysisResult(Base):
    """구조화된 분석 결과. `result_json` 은 `schemas.analysis.AnalysisResultSchema` 형태다."""

    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_job_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    result_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    #: 목록 표시용 비정규화 컬럼 (result_json 의 동명 필드와 같은 값)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, default=Severity.MEDIUM.value, server_default="medium"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped[AnalysisJob] = relationship(back_populates="result")


class AnalysisUsageRecord(Base):
    """토큰·비용 기록.

    `estimated_cost` 는 **추정**이며, 계산 시점의 단가표를 `pricing_snapshot` 에 함께 남긴다.
    정산 근거로 쓰지 않는다.
    """

    __tablename__ = "llm_usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_job_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_jobs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    #: 계산에 사용한 단가표 스냅샷 (app_settings 의 모델 단가표에서 복사)
    pricing_snapshot: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=UsageStatus.SUCCEEDED.value, server_default="succeeded"
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped[AnalysisJob] = relationship(back_populates="usage")

    __table_args__ = (Index("ix_llm_usage_records_model_created", "model", "created_at"),)


# ---------------------------------------------------------------- settings


class AppSetting(Base):
    """전역 설정 키-값 테이블.

    코드가 아니라 데이터로 두어 배포 없이 바꿀 수 있어야 하는 값들:
    - `daily_analysis_limit` — 전역 일일 분석 횟수 상한 (int)
    - `model_pricing` — 모델 단가표 (dict[model, {input_per_1k, output_per_1k, currency}])
    - `sample_retention_days` — error_samples 보존 기간 (int)
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONType, nullable=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


#: app_settings 의 예약 키. 문자열 오타를 막기 위해 코드에서는 이 상수만 쓴다.
SETTING_DAILY_ANALYSIS_LIMIT = "daily_analysis_limit"
SETTING_MODEL_PRICING = "model_pricing"
SETTING_SAMPLE_RETENTION_DAYS = "sample_retention_days"


__all__ = [
    "AnalysisJob",
    "AnalysisPolicy",
    "AnalysisResult",
    "AnalysisUsageRecord",
    "AppSetting",
    "Base",
    "ErrorGroup",
    "ErrorSample",
    "LLMConnection",
    "LokiConnection",
    "QueryRun",
    "SETTING_DAILY_ANALYSIS_LIMIT",
    "SETTING_MODEL_PRICING",
    "SETTING_SAMPLE_RETENTION_DAYS",
]
