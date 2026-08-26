"""분석·usage·보고서 트랙 테스트의 공통 fixture·헬퍼.

Phase 2 담당 트랙: **분석 플로우·usage·보고서** (`tests/test_analysis*.py`,
`tests/test_usage*.py` 소유 범위)

DB / TestClient fixture 는 정책 API 트랙이 만든 `tests/test_policies_fixtures.py` 를
그대로 재사용한다 (SQLite in-memory + `get_db` 오버라이드).

**실제 LLM API 는 절대 호출하지 않는다.** 어댑터 트랙이 만드는
`app.llm_providers.factory.build_llm_provider` 는 아직 구현 중이므로,
분석 트랙의 단일 mock 지점 `app.analysis.integrations.build_llm_provider` 를 patch 해
`FakeLLM` 을 돌려준다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

from sqlalchemy.orm import Session

from app.enums import AnalysisJobStatus, UsageStatus
from app.models import (
    SETTING_DAILY_ANALYSIS_LIMIT,
    SETTING_MODEL_PRICING,
    AnalysisJob,
    AnalysisUsageRecord,
    AppSetting,
    ErrorGroup,
    ErrorSample,
    LLMConnection,
)
from app.providers.llm import LLMAnalyzeResult, LLMError, LLMPrompt
from app.schemas.logrecord import ConnectionTestResult
from tests.test_policies_fixtures import (  # noqa: F401 - fixture 재수출
    NOW,
    client,
    db,
    engine,
    make_analysis_job,
    make_connection,
    make_error_group,
    make_policy,
    make_query_run,
    no_real_log_source,
    session_factory,
)

#: 설계 문서 "LLM 분석 설계" 의 응답 예시 그대로.
VALID_RESULT: dict[str, Any] = {
    "summary": "결제 게이트웨이 요청 시간이 초과되었습니다.",
    "severity": "high",
    "hypotheses": [
        {
            "cause": "외부 결제 API 지연 또는 장애",
            "confidence": 0.78,
            "evidence": ["504", "TimeoutError"],
        }
    ],
    "investigation_steps": ["결제 게이트웨이 상태 확인", "배포 이후 설정 변경 확인"],
    "mitigation": ["재시도 정책과 타임아웃 설정 점검"],
    "limitations": ["로그만으로 외부 서비스 장애를 확정할 수 없습니다."],
}

#: 단가표 (app_settings.model_pricing). 값은 테스트용이며 실제 단가가 아니다.
PRICING = {
    "gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.0006, "currency": "USD"},
}


def now() -> datetime:
    """실제 현재 시각. 일일 한도는 "오늘" 기준이라 고정 NOW 를 쓰면 안 된다."""
    return datetime.now(UTC)


# ------------------------------------------------------------- 가짜 LLM 어댑터


class FakeLLM:
    """`LLMProvider` 계약을 만족하는 최소 가짜 어댑터.

    `analyze()` 는 **원시 dict** 만 돌려준다 — 검증은 어댑터 밖 공통 경로의 몫이다.
    """

    provider_name = "openai"
    supports_structured_output = True

    def __init__(
        self,
        *,
        raw: dict[str, Any] | None = None,
        input_tokens: int = 1200,
        output_tokens: int = 340,
        error: Exception | None = None,
    ) -> None:
        self.raw = VALID_RESULT if raw is None else raw
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.error = error
        self.prompts: list[LLMPrompt] = []
        self.connections: list[Any] = []

    def test_connection(self) -> ConnectionTestResult:  # pragma: no cover - 이 트랙 밖
        return ConnectionTestResult(ok=True)

    def analyze(self, prompt: LLMPrompt) -> LLMAnalyzeResult:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return LLMAnalyzeResult(
            raw=self.raw, input_tokens=self.input_tokens, output_tokens=self.output_tokens
        )

    # --- 테스트 편의 ---

    @property
    def prompt(self) -> LLMPrompt:
        assert self.prompts, "LLM 이 호출되지 않았습니다."
        return self.prompts[-1]

    @property
    def prompt_text(self) -> str:
        """전송된 프롬프트 **전체** (system + user)."""
        prompt = self.prompt
        return f"{prompt.system or ''}\n{prompt.user}"


@contextmanager
def patched_llm(fake: FakeLLM | None = None) -> Iterator[FakeLLM]:
    """`build_llm_provider` 를 가짜로 바꾼다. 실제 API 호출은 일어나지 않는다."""
    provider = fake or FakeLLM()

    def _build(connection: Any) -> FakeLLM:
        provider.connections.append(connection)
        return provider

    with patch("app.analysis.integrations.build_llm_provider", side_effect=_build):
        yield provider


def llm_error(message: str = "429 Too Many Requests") -> LLMError:
    return LLMError(message, status_code=429)


# ----------------------------------------------------------------- ORM 헬퍼


def make_llm_connection(
    db: Session,
    *,
    name: str = "openai-default",
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    is_default: bool = True,
    active: bool = True,
) -> LLMConnection:
    connection = LLMConnection(
        name=name,
        provider=provider,
        model=model,
        base_url=None,
        # 평문 키는 어디에도 두지 않는다. 팩토리를 mock 하므로 복호화 경로도 타지 않는다.
        encrypted_api_key=None,
        is_default=is_default,
        active=active,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def add_samples(db: Session, group: ErrorGroup, lines: list[str]) -> list[ErrorSample]:
    """대표 로그를 직접 넣는다 (occurred_at 은 최신순 구분이 되도록 어긋나게)."""
    samples = []
    for index, line in enumerate(lines):
        sample = ErrorSample(
            error_group_id=group.id,
            occurred_at=NOW - timedelta(minutes=len(lines) - index),
            masked_log=line,
            labels={"service": group.service or ""},
            stacktrace=None,
            masking_rule_version="v1",
        )
        db.add(sample)
        samples.append(sample)
    db.commit()
    return samples


def make_job_row(
    db: Session,
    group: ErrorGroup,
    *,
    status: str = AnalysisJobStatus.SUCCEEDED.value,
    requested_at: datetime | None = None,
    connection: LLMConnection | None = None,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
) -> AnalysisJob:
    """분석 작업 행을 직접 넣는다 (한도·stale 검증용 — 실행 경로를 타지 않는다)."""
    job = AnalysisJob(
        error_group_id=group.id,
        llm_connection_id=connection.id if connection is not None else None,
        fingerprint=group.fingerprint,
        status=status,
        provider=provider,
        model=model,
        prompt_version="v1",
        requested_at=requested_at or now(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def make_usage_record(
    db: Session,
    job: AnalysisJob,
    *,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    input_tokens: int = 1000,
    output_tokens: int = 200,
    estimated_cost: Decimal | None = Decimal("0.001000"),
    latency_ms: int | None = 1200,
    status: str = UsageStatus.SUCCEEDED.value,
    created_at: datetime | None = None,
) -> AnalysisUsageRecord:
    record = AnalysisUsageRecord(
        analysis_job_id=job.id,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost=estimated_cost,
        pricing_snapshot=None,
        latency_ms=latency_ms,
        status=status,
        created_at=created_at or now(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def set_setting(db: Session, key: str, value: Any) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
        db.add(row)
    row.value = value
    db.commit()


def set_daily_limit(db: Session, limit: int) -> None:
    set_setting(db, SETTING_DAILY_ANALYSIS_LIMIT, limit)


def set_pricing(db: Session, pricing: dict[str, Any] | None = None) -> None:
    set_setting(db, SETTING_MODEL_PRICING, PRICING if pricing is None else pricing)


def usage_of(db: Session, job_id: int) -> AnalysisUsageRecord | None:
    db.expire_all()  # 백그라운드 작업이 **다른 세션**에서 쓴 행을 다시 읽는다.
    return (
        db.query(AnalysisUsageRecord)
        .filter(AnalysisUsageRecord.analysis_job_id == job_id)
        .one_or_none()
    )


__all__ = [
    "NOW",
    "PRICING",
    "VALID_RESULT",
    "FakeLLM",
    "add_samples",
    "llm_error",
    "make_llm_connection",
    "make_job_row",
    "make_usage_record",
    "now",
    "patched_llm",
    "set_daily_limit",
    "set_pricing",
    "set_setting",
    "usage_of",
]
