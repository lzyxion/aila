"""애플리케이션 설정.

모든 환경변수는 `AILA_` 접두사를 쓴다. (예: `AILA_DATABASE_URL`)

여기 있는 값은 **소스 무관 비용 통제**와 런타임 설정이다.
운영 중 바뀔 수 있는 값(일일 분석 한도, 모델 단가표, 샘플 보존 기간)은
환경변수가 아니라 DB `app_settings` 테이블에 둔다 — 여기 있는 동명의 값은
`app_settings` 에 아직 행이 없을 때의 기본값이다.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AILA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 기본 ---
    app_name: str = "AILA"
    environment: str = "local"
    debug: bool = False
    log_level: str = "INFO"

    # --- DB ---
    database_url: str = "postgresql+psycopg://aila:aila@localhost:5432/aila"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- 보안 ---
    # Fernet 키(urlsafe base64 32 bytes). 연결 secret·LLM API 키 암호화에 쓴다.
    # 미설정이면 앱은 뜨지만 암복호화 호출 시점에 예외가 난다.
    encryption_key: str | None = None

    # --- API ---
    api_prefix: str = "/api"
    # NoDecode: `AILA_CORS_ORIGINS=a,b` 같은 콤마 구분 문자열도 받기 위해 JSON 선파싱을 끈다.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # --- 로그 소스 조회 상한 (어댑터 밖의 소스 무관 비용 통제) ---
    query_timeout_seconds: float = 30.0
    max_query_range_minutes: int = 24 * 60
    max_lines_per_query: int = 5000

    # --- LLM ---
    llm_timeout_seconds: float = 120.0
    # 프롬프트 템플릿 버전. analysis_jobs.prompt_version 에 값으로 저장된다.
    prompt_version: str = "v1"
    # 전역 일일 분석 한도의 기본값 (app_settings 에 행이 없을 때).
    default_daily_analysis_limit: int = 50
    # 일일 한도의 "하루" 를 세는 기준 타임존 (app_settings 의 `timezone` 행이 없을 때).
    # UTC 자정 기준이면 한국에서는 오전 9 시에 카운터가 리셋돼 "하루" 감각과 어긋난다.
    default_timezone: str = "Asia/Seoul"
    # requested_at 기준 이 시간을 넘긴 running 작업은 failed 로 간주한다.
    # (BackgroundTasks 는 프로세스 재시작 시 진행 중 작업을 잃는다)
    analysis_job_stale_seconds: int = 900

    # 프롬프트에 싣는 마스킹된 대표 로그 수의 상한 (설계: "마스킹된 대표 로그 최대 3 개").
    prompt_max_samples: int = 3
    # 그룹당 대표 로그 수의 절대 상한. 정책이 프롬프트에 들어갈 양을 임의로 키우지 못하게 한다.
    max_samples_per_group_cap: int = 20

    # --- 보존 ---
    # error_samples 기본 보존 일수 (app_settings 에 행이 없을 때).
    default_sample_retention_days: int = 30

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """`AILA_CORS_ORIGINS=a,b` 형태의 콤마 구분 문자열도 받는다."""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
