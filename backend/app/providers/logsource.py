"""`LogSourceProvider` — 로그 소스 추상 인터페이스.

계약의 실체는 인터페이스가 아니라 **`fetch_logs()` 의 반환 타입**(`FetchResult`)이다.
`grouping` 이하 모듈은 `LogRecord` 만 보며, 소스별 라벨 이름 차이는 연결 설정의
`label_mapping` 으로 어댑터가 흡수한다.

경계 규칙 세 가지:

1. 소스 고유 지식(`| json` 파싱 실패, Loki 5,000 줄 한도, `__error__` 라벨)은
   **어댑터 안에 가둔다.** 밖으로는 `fetched`/`dropped`/`warnings` 로만 올린다.
2. 기간·라인 수 상한, 타임아웃, 일일 분석 한도는 소스 무관 비용 통제이므로
   **어댑터 밖**(정책 검증 / `app.config`)에 둔다.
3. 소스별 능력 차이는 capability 플래그로 선언하고, 없는 기능은 UI 에서 비활성화한다.

MVP 에서 두 번째 어댑터는 만들지 않는다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.logrecord import (
    ConnectionTestResult,
    CountSeries,
    FetchResult,
    TimeRange,
)


class LogSourceError(RuntimeError):
    """로그 소스 조회 실패의 공통 예외 타입. 어댑터는 소스 고유 예외를 이것으로 감싼다."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LogSourceProvider(ABC):
    """로그 소스 어댑터 인터페이스.

    구현체는 생성자에서 연결 정보(base_url, 인증, `label_mapping`)를 받는다.
    복호화된 secret 을 받는 것은 호출자의 책임이다 (`app.crypto.decrypt`).
    """

    #: capability 플래그 — 구현체가 오버라이드한다. UI 비활성화 판단에 쓴다.
    supports_count: bool = True
    supports_label_discovery: bool = True
    #: `service_presence()` 를 구현했는가. False 면 수집 중단 확인을 조용히 건너뛴다
    #: (경고도 남기지 않는다 — 소스가 못 하는 일은 정책 설정의 문제가 아니다).
    supports_presence: bool = False

    #: 소스 종류 식별자 (`log_source_connections.source_type` 값과 같아야 한다).
    source_type: str = "loki"

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """연결·인증 테스트."""

    @abstractmethod
    def list_labels(self) -> dict[str, list[str]]:
        """정책 작성 UI 용 라벨·값 탐색.

        반환: 라벨명 -> 값 목록. 값 탐색을 지원하지 않으면 빈 리스트를 담는다.
        `supports_label_discovery=False` 인 구현체는 빈 dict 를 돌려준다.
        """

    @abstractmethod
    def fetch_logs(self, query: str, range: TimeRange, limit: int) -> FetchResult:
        """로그 라인 조회.

        `query` 는 소스 고유 문법 그대로다. `limit` 은 이미 정책 상한으로 clamp 된 값이
        들어온다고 가정하되, 어댑터는 소스 자체 한도(예: Loki 5,000 줄)에 걸린 경우
        `truncated=True` 와 `warnings` 로 알린다.

        조회 결과는 **그룹화와 대표 샘플 추출용**이다. 건수·추이 집계에 쓰지 말 것 —
        상한에 걸려 실제보다 적게 나온다.
        """

    @abstractmethod
    def count_over_time(self, query: str, range: TimeRange, step: int) -> CountSeries:
        """건수·추이 metric 조회. `step` 은 초 단위.

        `supports_count=False` 인 구현체는 `NotImplementedError` 를 던진다.
        """

    def service_presence(self, services: list[str], range: TimeRange) -> set[str]:  # noqa: A002
        """`services` (표준 필드 `service` 기준 이름) 중 이 기간에 로그를 **한 줄이라도**
        내보낸 서비스의 집합을 돌려준다.

        수집 중단 확인용이다 — 호출자는 `services - 반환값` 을 부재로 판정한다.
        소스 라벨명 차이는 어댑터가 `label_mapping` 으로 흡수하고, 셀렉터 구성
        (이름 이스케이프 포함)도 어댑터 몫이다. `supports_presence=False` 인 구현체는
        이 기본 구현(NotImplementedError)을 그대로 둔다.
        """
        raise NotImplementedError


__all__ = ["LogSourceError", "LogSourceProvider"]
