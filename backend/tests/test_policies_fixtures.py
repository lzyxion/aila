"""정책 API 트랙 테스트의 공통 fixture·헬퍼.

Phase 1 담당 트랙: **정책 API** (`tests/test_policies*.py` 소유 범위)

`tests/conftest.py` 는 트랙 공용 파일이라 건드리지 않는다. 대신 여기에 fixture 를
모아 두고 `test_error_groups.py` / `test_dashboard.py` 가 이름으로 가져다 쓴다.

그룹화·마스킹 트랙과 Loki 어댑터 트랙이 **동시에** 계약을 구현 중이므로, 여기서는
그들의 실제 코드를 import 하지 않는다. `app.policies.integrations` 를 mock 지점으로
삼고, `GroupedError` / `GroupedSample` 은 계약 시그니처와 같은 속성을 가진 가짜 객체로
만든다 (duck typing — 서비스 코드는 속성 접근만 한다).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.enums import AnalysisJobStatus, AuthType, QueryRunStatus, Severity, SourceType
from app.main import create_app
from app.models import (
    AnalysisJob,
    AnalysisPolicy,
    AnalysisResult,
    ErrorGroup,
    ErrorSample,
    LogSourceConnection,
    QueryRun,
)
from app.schemas.logrecord import CountPoint, CountSeries, FetchResult, FetchWarning, LogRecord

#: 테스트 전반의 고정 기준 시각 (기간 clamp 계산을 결정적으로 만든다).
NOW = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)


# ------------------------------------------------------------------ DB / app


@pytest.fixture()
def engine():
    """SQLite in-memory. StaticPool 이라야 여러 세션이 같은 DB 를 본다."""
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@pytest.fixture()
def db(session_factory) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def no_real_log_source() -> Iterator[Any]:
    """실제 로그 소스로 나가는 경로를 전부 가짜로 막는다 (autouse).

    그룹 상세의 발생 추이와 분석 프롬프트의 "최근 추이"는 둘 다 `count_over_time` 을
    부른다. 막지 않으면 fixture 의 `http://loki:3100` 으로 진짜 요청이 나가 테스트가
    DNS 해석 시간만큼 느려지고, 네트워크 환경에 따라 결과가 달라진다.

    개별 테스트가 자기 provider 를 쓰려면 그 안에서 다시 `patch` 하면 된다 (중첩된
    patch 가 이긴다).

    이 fixture 를 쓰는 모듈은 이름을 **명시적으로 import** 해야 한다 —
    autouse 는 모듈 네임스페이스에 들어와 있어야 적용된다.
    """
    with patch(
        "app.policies.integrations.build_provider",
        side_effect=lambda connection: FakeLogSource(),
    ) as build:
        yield build


@pytest.fixture()
def client(session_factory) -> Iterator[TestClient]:
    api = create_app()

    def _override_get_db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    api.dependency_overrides[get_db] = _override_get_db
    with TestClient(api) as test_client:
        yield test_client
    api.dependency_overrides.clear()


# ----------------------------------------------------------------- ORM 헬퍼


def make_connection(
    db: Session,
    *,
    name: str = "loki-local",
    active: bool = True,
    expected_services: list[str] | None = None,
) -> LogSourceConnection:
    connection = LogSourceConnection(
        name=name,
        source_type=SourceType.LOKI.value,
        base_url="http://loki:3100",
        auth_type=AuthType.NONE.value,
        label_mapping={"app": "service", "env": "environment"},
        active=active,
        expected_services=expected_services,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def make_policy(db: Session, connection: LogSourceConnection, **overrides: Any) -> AnalysisPolicy:
    values: dict[str, Any] = {
        "log_source_connection_id": connection.id,
        "name": "payment-api ERROR",
        "query": '{service="payment-api"} | json | level="ERROR"',
        "default_range_minutes": 60,
        "max_lines": 1000,
        "exclusions": [],
        "max_samples_per_group": 3,
        "allow_ai_analysis": True,
        "daily_analysis_limit": None,
        "active": True,
    }
    values.update(overrides)
    policy = AnalysisPolicy(**values)
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


def make_query_run(
    db: Session,
    policy: AnalysisPolicy,
    *,
    status: str = QueryRunStatus.SUCCEEDED.value,
    started_at: datetime | None = None,
    range_minutes: int = 60,
) -> QueryRun:
    started = started_at or NOW
    run = QueryRun(
        policy_id=policy.id,
        started_at=started,
        finished_at=started,
        range_start=started - timedelta(minutes=range_minutes),
        range_end=started,
        status=status,
        fetched_count=0,
        dropped_count=0,
        warnings=[],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def make_error_group(
    db: Session,
    run: QueryRun,
    *,
    fingerprint: str,
    count: int = 1,
    service: str | None = "payment-api",
    normalized_message: str = "TimeoutError: payment gateway timed out",
    samples: int = 0,
    labels: dict[str, str] | None = None,
) -> ErrorGroup:
    group = ErrorGroup(
        query_run_id=run.id,
        fingerprint=fingerprint,
        service=service,
        environment="staging",
        error_type="TimeoutError",
        normalized_message=normalized_message,
        count=count,
        first_seen=NOW - timedelta(minutes=30),
        last_seen=NOW,
        labels=(
            {"service": service or "", "environment": "staging"}
            if labels is None
            else labels
        ),
        top_stack_frame="payments/gateway.py:88",
        normalization_rule_version="v1",
    )
    for index in range(samples):
        group.samples.append(
            ErrorSample(
                occurred_at=NOW - timedelta(minutes=index),
                masked_log=f"TimeoutError id=<UUID> token=<REDACTED> #{index}",
                labels={"service": service or ""},
                stacktrace=None,
                masking_rule_version="v1",
            )
        )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def make_analysis_job(
    db: Session,
    group: ErrorGroup,
    *,
    fingerprint: str | None = None,
    status: str = AnalysisJobStatus.SUCCEEDED.value,
    requested_at: datetime | None = None,
    severity: str | None = Severity.HIGH.value,
    summary: str = "결제 게이트웨이 요청 시간이 초과되었습니다.",
) -> AnalysisJob:
    """분석 이력 fixture. fingerprint 기준 조인을 검증하기 위해 직접 넣는다."""
    job = AnalysisJob(
        error_group_id=group.id,
        llm_connection_id=None,
        fingerprint=fingerprint or group.fingerprint,
        status=status,
        provider="openai",
        model="gpt-4o-mini",
        prompt_version="v1",
        requested_at=requested_at or NOW,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if severity is not None:
        db.add(
            AnalysisResult(
                analysis_job_id=job.id,
                result_json={
                    "summary": summary,
                    "severity": severity,
                    "hypotheses": [{"cause": "외부 결제 API 지연", "confidence": 0.7, "evidence": []}],
                    "investigation_steps": [],
                    "mitigation": [],
                    "limitations": ["로그만으로 확정할 수 없습니다."],
                },
                summary=summary,
                severity=severity,
            )
        )
        db.commit()
    return job


# --------------------------------------------- 상대 트랙 계약의 가짜 구현


def log_record(message: str, *, minutes_ago: int = 0, service: str = "payment-api") -> LogRecord:
    return LogRecord(
        timestamp=NOW - timedelta(minutes=minutes_ago),
        message=message,
        labels={"service": service, "environment": "staging", "level": "ERROR"},
        service=service,
        environment="staging",
        level="ERROR",
    )


def grouped_sample(masked_log: str, *, minutes_ago: int = 0) -> SimpleNamespace:
    """`app.grouping.service.GroupedSample` 계약과 같은 속성을 갖는 가짜 객체."""
    return SimpleNamespace(
        occurred_at=NOW - timedelta(minutes=minutes_ago),
        masked_log=masked_log,
        labels={"service": "payment-api"},
        stacktrace=None,
        masking_rule_version="v1",
    )


def grouped_error(
    fingerprint: str,
    *,
    count: int = 1,
    service: str | None = "payment-api",
    normalized_message: str = "TimeoutError: payment gateway timed out",
    samples: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    """`app.grouping.service.GroupedError` 계약과 같은 속성을 갖는 가짜 객체."""
    return SimpleNamespace(
        fingerprint=fingerprint,
        service=service,
        environment="staging",
        error_type="TimeoutError",
        normalized_message=normalized_message,
        count=count,
        first_seen=NOW - timedelta(minutes=30),
        last_seen=NOW,
        labels={"service": service or "", "environment": "staging"},
        top_stack_frame="payments/gateway.py:88",
        samples=samples if samples is not None else [grouped_sample("masked line")],
    )


class FakeLogSource:
    """`LogSourceProvider` 계약을 만족하는 최소 가짜 어댑터 (mock 반환용)."""

    supports_count = True
    supports_label_discovery = True
    supports_presence = False
    source_type = SourceType.LOKI.value

    def __init__(
        self,
        *,
        fetch_result: FetchResult | None = None,
        fetch_error: Exception | None = None,
        count_series: CountSeries | None = None,
        count_error: Exception | None = None,
        supports_count: bool = True,
        supports_presence: bool = False,
        present_services: set[str] | None = None,
        presence_error: Exception | None = None,
    ) -> None:
        self.fetch_result = fetch_result or FetchResult()
        self.fetch_error = fetch_error
        self.count_series = count_series or CountSeries(step_seconds=300)
        self.count_error = count_error
        self.supports_count = supports_count
        self.supports_presence = supports_presence
        self.present_services = present_services if present_services is not None else set()
        self.presence_error = presence_error
        self.fetch_calls: list[tuple[str, Any, int]] = []
        self.count_calls: list[tuple[str, Any, int]] = []
        self.presence_calls: list[tuple[list[str], Any]] = []
        #: 분모 쿼리처럼 쿼리마다 다른 시리즈를 돌려줘야 할 때 쓴다 (query -> CountSeries).
        self.count_series_by_query: dict[str, CountSeries] = {}

    def test_connection(self):  # pragma: no cover - 이 트랙 테스트에서 쓰지 않는다
        raise NotImplementedError

    def list_labels(self):  # pragma: no cover - 이 트랙 테스트에서 쓰지 않는다
        raise NotImplementedError

    def fetch_logs(self, query: str, range: Any, limit: int) -> FetchResult:  # noqa: A002
        self.fetch_calls.append((query, range, limit))
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.fetch_result

    def count_over_time(self, query: str, range: Any, step: int) -> CountSeries:  # noqa: A002
        self.count_calls.append((query, range, step))
        if query in self.count_series_by_query:
            series = self.count_series_by_query[query]
            if isinstance(series, Exception):  # pragma: no cover - 방어
                raise series
            return series
        if self.count_error is not None:
            raise self.count_error
        return self.count_series

    def service_presence(self, services: list[str], range: Any) -> set[str]:  # noqa: A002
        self.presence_calls.append((list(services), range))
        if self.presence_error is not None:
            raise self.presence_error
        return set(self.present_services)


def count_series(*values: tuple[str, float], step_seconds: int = 300) -> CountSeries:
    """(service, value) 목록으로 CountSeries 를 만든다."""
    return CountSeries(
        step_seconds=step_seconds,
        points=[
            CountPoint(
                timestamp=NOW - timedelta(seconds=step_seconds * index),
                value=value,
                labels={"service": service},
            )
            for index, (service, value) in enumerate(values)
        ],
        warnings=[],
    )


__all__ = [
    "NOW",
    "FakeLogSource",
    "FetchResult",
    "FetchWarning",
    "count_series",
    "grouped_error",
    "grouped_sample",
    "log_record",
    "make_analysis_job",
    "make_connection",
    "make_error_group",
    "make_policy",
    "make_query_run",
    "no_real_log_source",
]
