"""Phase 0 계약 테스트.

여기서 검증하는 것은 동작이 아니라 **계약**이다 — 모듈이 import 되는가, 정규화 레코드와
분석 결과 스키마가 왕복하는가, API 초안의 라우트가 전부 존재하는가, 마이그레이션이
모델과 일치하는가. Phase 1 트랙이 이 파일을 깨뜨렸다면 공유 계약을 건드린 것이다.

DB 서버는 필요 없다 (SQLite / 미접속 구조).
"""

from __future__ import annotations

import importlib
import pkgutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect

import app as app_package
from app.db import Base
from app.enums import AnalysisJobStatus, Severity, SourceType
from app.main import create_app
from app.providers.llm import LLMAnalyzeResult, LLMPrompt, LLMProvider
from app.providers.logsource import LogSourceProvider
from app.schemas.analysis import (
    AnalysisResultSchema,
    analysis_json_schema,
    parse_analysis_result,
)
from app.schemas.logrecord import (
    ConnectionTestResult,
    CountPoint,
    CountSeries,
    FetchResult,
    FetchWarning,
    LogRecord,
    TimeRange,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ imports


def test_all_app_modules_import() -> None:
    """`app` 하위 모든 모듈이 import 된다 (순환 import·오타 조기 발견)."""
    failures: list[str] = []
    for module in pkgutil.walk_packages(app_package.__path__, prefix="app."):
        try:
            importlib.import_module(module.name)
        except Exception as exc:  # pragma: no cover - 실패 시 메시지에 담는다
            failures.append(f"{module.name}: {exc!r}")
    assert not failures, failures


def test_placeholder_packages_exist() -> None:
    """Phase 1 트랙이 채울 빈 구현 패키지가 자리에 있다."""
    for name in ("app.loki", "app.grouping", "app.masking"):
        assert importlib.import_module(name) is not None


# -------------------------------------------------- 정규화 로그 레코드 계약


def test_logrecord_round_trip() -> None:
    record = LogRecord(
        timestamp=datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
        message="TimeoutError: payment gateway timed out",
        labels={"service": "payment-api", "environment": "staging", "level": "ERROR"},
        service="payment-api",
        environment="staging",
        level="ERROR",
    )
    restored = LogRecord.model_validate(record.model_dump())
    assert restored == record
    assert LogRecord.model_validate_json(record.model_dump_json()) == record


def test_logrecord_standard_fields_are_optional() -> None:
    record = LogRecord(timestamp=datetime.now(UTC), message="boom")
    assert record.service is None
    assert record.environment is None
    assert record.level is None
    assert record.labels == {}


def test_logrecord_rejects_unknown_field() -> None:
    """계약 외 필드를 몰래 실어 보내지 못하게 한다 (extra='forbid')."""
    with pytest.raises(ValidationError):
        LogRecord(timestamp=datetime.now(UTC), message="x", raw_log="원본 로그")


def test_fetch_result_metadata_round_trip() -> None:
    result = FetchResult(
        records=[LogRecord(timestamp=datetime.now(UTC), message="a")],
        dropped=2,
        warnings=[FetchWarning(code="parse_error", message="| json 파싱 실패", count=2)],
        truncated=True,
    )
    assert result.fetched == 1  # records 로부터 자동 채움
    restored = FetchResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.warnings[0].code == "parse_error"


def test_time_range_and_count_series() -> None:
    start = datetime(2026, 8, 26, tzinfo=UTC)
    time_range = TimeRange(start=start, end=start + timedelta(hours=1))
    assert time_range.duration == timedelta(hours=1)

    with pytest.raises(ValidationError):
        TimeRange(start=start, end=start)

    series = CountSeries(
        step_seconds=300,
        points=[
            CountPoint(timestamp=start, value=3.0, labels={"service": "payment-api"}),
            CountPoint(timestamp=start + timedelta(minutes=5), value=4.0),
        ],
    )
    assert series.total == 7.0
    assert CountSeries.model_validate_json(series.model_dump_json()) == series


# ------------------------------------------------------ 분석 결과 스키마 계약


DESIGN_DOC_EXAMPLE = {
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


def test_analysis_result_round_trip_with_design_doc_example() -> None:
    parsed = parse_analysis_result(DESIGN_DOC_EXAMPLE)
    assert parsed.severity is Severity.HIGH
    assert parsed.hypotheses[0].confidence == 0.78
    assert parsed.model_dump(mode="json") == DESIGN_DOC_EXAMPLE
    assert AnalysisResultSchema.model_validate_json(parsed.model_dump_json()) == parsed


@pytest.mark.parametrize("missing", ["hypotheses", "limitations"])
def test_analysis_result_requires_hypotheses_and_limitations(missing: str) -> None:
    """단정 금지 장치 — 두 필드는 스키마 차원에서 필수다."""
    payload = {key: value for key, value in DESIGN_DOC_EXAMPLE.items() if key != missing}
    with pytest.raises(ValidationError):
        parse_analysis_result(payload)


@pytest.mark.parametrize("empty", ["hypotheses", "limitations"])
def test_analysis_result_rejects_empty_required_lists(empty: str) -> None:
    payload = dict(DESIGN_DOC_EXAMPLE) | {empty: []}
    with pytest.raises(ValidationError):
        parse_analysis_result(payload)


def test_analysis_result_confidence_bounds() -> None:
    payload = dict(DESIGN_DOC_EXAMPLE)
    payload["hypotheses"] = [{"cause": "x", "confidence": 1.5, "evidence": []}]
    with pytest.raises(ValidationError):
        parse_analysis_result(payload)


def test_analysis_json_schema_shape() -> None:
    """프로바이더 structured outputs 로 넘길 스키마가 필수 필드를 담고 있다."""
    schema = analysis_json_schema()
    assert set(schema["required"]) >= {"summary", "severity", "hypotheses", "limitations"}
    assert "investigation_steps" in schema["properties"]
    assert "mitigation" in schema["properties"]


# ------------------------------------------------------------ 프로바이더 계약


def test_logsource_provider_is_abstract_with_capability_flags() -> None:
    assert LogSourceProvider.supports_count is True
    assert LogSourceProvider.supports_label_discovery is True
    required = {"test_connection", "list_labels", "fetch_logs", "count_over_time"}
    assert required <= LogSourceProvider.__abstractmethods__
    with pytest.raises(TypeError):
        LogSourceProvider()  # type: ignore[abstract]


def test_service_presence_is_an_optional_capability() -> None:
    """Phase 7: 수집 중단 확인은 **선택** 능력이다.

    기본값이 `False` 여야 못 하는 소스를 조용히 건너뛸 수 있다 — 추상 메서드로
    올리면 기존 어댑터가 전부 깨지고, 기본값이 `True` 면 확인하지도 않은 소스에서
    "부재 없음" 이라는 잘못된 안심을 준다.
    """
    assert LogSourceProvider.supports_presence is False
    assert "service_presence" not in LogSourceProvider.__abstractmethods__
    now = datetime.now(UTC)
    with pytest.raises(NotImplementedError):
        LogSourceProvider.service_presence(
            None, ["payment-api"], TimeRange(start=now, end=now + timedelta(minutes=5))
        )


def test_llm_provider_contract() -> None:
    assert {"test_connection", "analyze"} <= LLMProvider.__abstractmethods__
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[abstract]


def test_llm_analyze_result_unpacks_as_tuple() -> None:
    """`analyze()` 는 원시 JSON 과 토큰 수만 돌려준다 — 검증은 어댑터 밖 공통 경로."""
    result = LLMAnalyzeResult(raw=DESIGN_DOC_EXAMPLE, input_tokens=1200, output_tokens=340)
    raw, input_tokens, output_tokens = result
    assert raw is DESIGN_DOC_EXAMPLE
    assert (input_tokens, output_tokens) == (1200, 340)


def test_llm_prompt_defaults_to_analysis_schema() -> None:
    prompt = LLMPrompt(user="마스킹된 대표 로그 3 개…")
    assert prompt.json_schema == analysis_json_schema()
    assert prompt.prompt_version == "v1"


def test_fake_providers_satisfy_the_interfaces() -> None:
    """구현체가 인터페이스를 실제로 만족시킬 수 있는지 (Phase 1 어댑터의 형태 확인)."""

    class FakeLogSource(LogSourceProvider):
        supports_count = False
        source_type = SourceType.LOKI.value

        def test_connection(self) -> ConnectionTestResult:
            return ConnectionTestResult(ok=True, message="ok")

        def list_labels(self) -> dict[str, list[str]]:
            return {"service": ["payment-api"]}

        def fetch_logs(self, query: str, range: TimeRange, limit: int) -> FetchResult:
            return FetchResult(records=[LogRecord(timestamp=range.start, message=query)])

        def count_over_time(self, query: str, range: TimeRange, step: int) -> CountSeries:
            raise NotImplementedError

    class FakeLLM(LLMProvider):
        provider_name = "openai"

        def test_connection(self) -> ConnectionTestResult:
            return ConnectionTestResult(ok=True)

        def analyze(self, prompt: LLMPrompt) -> LLMAnalyzeResult:
            return LLMAnalyzeResult(raw=DESIGN_DOC_EXAMPLE, input_tokens=1, output_tokens=1)

    now = datetime.now(UTC)
    source = FakeLogSource()
    fetched = source.fetch_logs("{service=\"x\"}", TimeRange(start=now, end=now + timedelta(minutes=5)), 10)
    assert fetched.fetched == 1
    assert source.supports_count is False

    raw, _, _ = FakeLLM().analyze(LLMPrompt(user="hi"))
    assert parse_analysis_result(raw).severity is Severity.HIGH


# ------------------------------------------------------------------- crypto


def test_crypto_round_trip_and_masking() -> None:
    from app.crypto import decrypt, encrypt, mask_secret

    token = encrypt("sk-super-secret-key")
    assert token != "sk-super-secret-key"
    assert decrypt(token) == "sk-super-secret-key"
    assert mask_secret("sk-super-secret-key") == "*" * 15 + "-key"


def test_crypto_rejects_foreign_token() -> None:
    from app.crypto import DecryptionError, decrypt

    with pytest.raises(DecryptionError):
        decrypt("not-a-fernet-token")


# ------------------------------------------------------------- FastAPI 기동


@pytest.fixture(scope="module")
def api() -> FastAPI:
    return create_app()


@pytest.fixture(scope="module")
def client(api: FastAPI) -> TestClient:
    return TestClient(api, raise_server_exceptions=False)


def _routes(api: FastAPI) -> set[tuple[str, str]]:
    """(METHOD, path) 집합. OpenAPI 스키마에서 읽는다 (FastAPI 내부 구조에 의존하지 않도록)."""
    schema = api.openapi()
    return {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method.upper() not in {"HEAD", "OPTIONS"}
    }


#: 설계 문서 "API 초안" 의 13 행 전부. Phase 1 에서 이 목록이 줄어들면 안 된다.
#: Phase 9 에서 `/api/loki-connections` 계열만 `/api/log-source-connections` 로 개명했다
#: (별칭 없는 순수 rename — 설계 문서의 옛 경로와 대조할 때 이 한 줄을 보라).
DESIGN_DOC_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/log-source-connections/test"),
    ("GET", "/api/llm-connections"),
    ("POST", "/api/llm-connections"),
    ("POST", "/api/llm-connections/test"),
    ("GET", "/api/policies"),
    ("POST", "/api/policies"),
    ("GET", "/api/policies/{policy_id}"),
    ("PATCH", "/api/policies/{policy_id}"),
    ("DELETE", "/api/policies/{policy_id}"),
    ("POST", "/api/policies/{policy_id}/query-runs"),
    ("GET", "/api/query-runs/{run_id}/error-groups"),
    ("GET", "/api/error-groups/{group_id}"),
    ("POST", "/api/error-groups/{group_id}/analysis-jobs"),
    ("GET", "/api/analysis-jobs/{job_id}"),
    ("GET", "/api/analysis-jobs/{job_id}/report"),
    ("GET", "/api/dashboard/overview"),
    ("GET", "/api/usage"),
}

#: Phase 0 에서 추가한 라우트 (설계 문서 표에는 없으나 화면 구성·데이터 모델상 필요).
ADDITIONAL_ROUTES: set[tuple[str, str]] = {
    ("GET", "/api/log-source-connections"),
    ("POST", "/api/log-source-connections"),
    ("GET", "/api/log-source-connections/{connection_id}"),
    ("PATCH", "/api/log-source-connections/{connection_id}"),
    ("DELETE", "/api/log-source-connections/{connection_id}"),
    ("GET", "/api/log-source-connections/{connection_id}/labels"),
    ("GET", "/api/llm-connections/{connection_id}"),
    ("PATCH", "/api/llm-connections/{connection_id}"),
    ("DELETE", "/api/llm-connections/{connection_id}"),
    ("POST", "/api/policies/preview"),
    ("GET", "/api/query-runs/{run_id}"),
    ("GET", "/health"),
}

#: Phase 4 (1 차 사용 피드백) 에서 추가한 라우트. 프론트 트랙이 소비하는 계약이라
#: 한쪽이 조용히 없애면 화면이 폴백 상태로 굳는다 — 여기서 존재를 못 박는다.
PHASE_4_ROUTES: set[tuple[str, str]] = {
    # 조회지만 POST 다 — api_key 를 쿼리스트링에 실으면 평문 키가 액세스 로그에 남는다.
    ("POST", "/api/llm-connections/models"),
    ("GET", "/api/policies/{policy_id}/query-runs"),
}


#: Phase 5 (인증·스케줄러·통합 대시보드) 에서 추가한 라우트.
#: 프론트가 401 을 가로채 `/login` 으로 보내는 계약이 이 세 개 위에 서 있다.
PHASE_5_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/users"),
    ("GET", "/api/dashboard/summary"),
}


#: Phase 7 (대시보드 지표 확장) 라우트. 한도 게이지가 이 위에 서 있다 —
#: 게이지는 429 한도 검사와 같은 계산(`analysis.service.daily_usage`)을 보여 준다.
PHASE_7_ROUTES: set[tuple[str, str]] = {
    ("GET", "/api/usage/daily-limit"),
}


def test_app_boots_and_health_is_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_schema_generates(api: FastAPI) -> None:
    schema = api.openapi()
    assert schema["info"]["title"].startswith("AILA")
    assert schema["paths"]


def test_all_design_doc_routes_exist(api: FastAPI) -> None:
    missing = (
        DESIGN_DOC_ROUTES | ADDITIONAL_ROUTES | PHASE_4_ROUTES | PHASE_5_ROUTES | PHASE_7_ROUTES
    ) - _routes(api)
    assert not missing, f"누락된 라우트: {sorted(missing)}"


def test_request_validation_still_applies(client: TestClient) -> None:
    """스텁이어도 요청 검증은 계약대로 동작한다 (422)."""
    response = client.post("/api/llm-connections", json={"name": "x"})
    assert response.status_code == 422


# ------------------------------------------------------- 모델 / 마이그레이션


def test_models_cover_design_doc_tables() -> None:
    expected = {
        "log_source_connections",
        "llm_connections",
        "analysis_policies",
        "query_runs",
        "error_groups",
        "error_samples",
        "analysis_jobs",
        "analysis_results",
        "llm_usage_records",
        "app_settings",
        # Phase 5 인증 (revision 0004). 세션은 서버 행으로 둔다 —
        # 무상태 서명 토큰으로는 "로그아웃 무효화" 를 만들 수 없다.
        "users",
        "user_sessions",
    }
    assert expected == set(Base.metadata.tables)


def test_error_samples_stores_no_raw_log() -> None:
    """계약: 원본(마스킹 전) 로그는 저장하지 않는다."""
    columns = set(Base.metadata.tables["error_samples"].columns.keys())
    assert "masked_log" in columns
    assert not columns & {"raw_log", "raw_message", "original_log", "message"}


def test_analysis_jobs_duplicates_provider_and_model() -> None:
    """연결 설정이 바뀌어도 과거 이력이 실제 사용 모델을 유지해야 한다."""
    columns = Base.metadata.tables["analysis_jobs"].columns
    assert {"llm_connection_id", "provider", "model", "prompt_version"} <= set(columns.keys())
    assert not columns["provider"].nullable
    assert not columns["model"].nullable


def test_error_groups_are_scoped_to_a_query_run() -> None:
    columns = Base.metadata.tables["error_groups"].columns
    assert not columns["query_run_id"].nullable
    assert columns["fingerprint"].index or any(
        "fingerprint" in index.name for index in Base.metadata.tables["error_groups"].indexes
    )


def test_initial_migration_matches_models(tmp_path: Path) -> None:
    """`alembic upgrade head` 결과가 모델 메타데이터와 같은 테이블·컬럼을 만든다."""
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "migration_check.db"
    url = f"sqlite+pysqlite:///{db_path.as_posix()}"

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")

    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        migrated = set(inspector.get_table_names()) - {"alembic_version"}
        assert migrated == set(Base.metadata.tables)
        for table_name, table in Base.metadata.tables.items():
            migrated_columns = {column["name"] for column in inspector.get_columns(table_name)}
            assert migrated_columns == set(table.columns.keys()), table_name
    finally:
        engine.dispose()


# --------------------------------------------------------------------- enums


def test_enum_values_are_stable_strings() -> None:
    """DB 에 문자열로 저장되므로 값이 바뀌면 기존 행이 깨진다."""
    assert Severity.HIGH.value == "high"
    assert AnalysisJobStatus.RUNNING.value == "running"
    assert SourceType.LOKI.value == "loki"
