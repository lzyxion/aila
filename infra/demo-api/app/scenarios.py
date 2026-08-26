"""장애 시나리오 6 종.

설계 문서 "데모 및 테스트 환경" 표와 1:1 로 대응한다.

| id                  | 예상 오류                   | 검증 대상                    |
| ------------------- | --------------------------- | ---------------------------- |
| payment-timeout     | TimeoutError, 504           | 빈도 급증 감지·LLM 원인 가설 |
| db-connection       | DatabaseConnectionError     | 오류 그룹화·대응 절차        |
| auth-expired        | 401, JWT expired            | 서비스·기간 필터             |
| null-reference      | stack trace                 | 스택 프레임 추출             |
| post-deploy-spike   | release 라벨 변화           | 배포 전후 비교               |
| secret-leak         | Bearer token, email         | 마스킹 검증                  |

각 시나리오의 기대 원인·확인 절차는 `infra/scenarios/<id>/expected-analysis.md` 에 있다.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field

from .logging_setup import (
    ENVIRONMENT,
    SERVICE_NAME,
    emit,
    emit_raw,
    format_exception,
    new_request_id,
)

# ---------------------------------------------------------------------------
# 가짜 비밀값 — 마스킹 검증 전용
#
# **전부 명백한 가짜다.** 실제 자격증명을 여기에 넣으면 안 된다.
#  - JWT: 서명 자리에 사람이 읽을 수 있는 FAKE 문자열을 넣어 진짜와 구별된다.
#  - 도메인: example.com / example.invalid (RFC 2606·6761 예약)
#  - 카드번호: 4111-1111-1111-1111 (업계 공용 테스트 번호)
# 이 값들이 마스킹 후 페이로드에 남아 있으면 안 된다 — 그것을 단언하는 자동 테스트가
# 그룹화·마스킹 트랙의 책임이다.
# ---------------------------------------------------------------------------

FAKE_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".ZmFrZS1kZW1vLXBheWxvYWQtbm90LWEtcmVhbC10b2tlbg"
    ".FAKE0SIGNATURE0FOR0AILA0MASKING0DEMO0ONLY"
)
FAKE_BEARER = f"Bearer {FAKE_JWT}"
FAKE_EMAIL = "demo.user@example.com"
FAKE_EMAIL_ALT = "ops-oncall@example.org"
FAKE_PHONE = "010-0000-0000"
FAKE_CARD = "4111-1111-1111-1111"
FAKE_API_KEY = "sk-demo-FAKE000000000000000000000000000000"
FAKE_DB_URL = "postgresql://demo_user:not-a-real-password@db.example.invalid:5432/demo"
FAKE_COOKIE = "session=FAKE-SESSION-COOKIE-VALUE-DEMO-ONLY; Path=/; HttpOnly"

# 논리적 하위 서비스. JSON 본문의 service 가 Alloy 에서 컨테이너 라벨을 덮어쓰므로
# 컨테이너 하나가 세 개의 Loki 스트림을 만든다.
PAYMENT_SERVICE = SERVICE_NAME
ORDER_SERVICE = os.getenv("DEMO_ORDER_SERVICE", "order-api")
AUTH_SERVICE = os.getenv("DEMO_AUTH_SERVICE", "auth-api")

BASE_RELEASE = os.getenv("DEMO_RELEASE", "v1.4.2")
NEXT_RELEASE = os.getenv("DEMO_NEXT_RELEASE", "v1.5.0")
# 배포 직후 오류가 몰리는 구간의 길이
DEPLOY_SPIKE_SECONDS = int(os.getenv("DEMO_DEPLOY_SPIKE_SECONDS", "180"))

_GATEWAYS = ("pg-alpha", "pg-beta")
_UPSTREAM = "https://gateway.example.invalid/v2/charges"


# ---------------------------------------------------------------------------
# 배포 상태
# ---------------------------------------------------------------------------


@dataclass
class DeployState:
    """현재 release 와 '배포 직후' 구간 여부.

    이 값이 로그의 `release` 필드로 나가고, Alloy 가 라벨로 올린다.
    배포 전후 비교는 이 라벨 하나로 성립한다.
    """

    release: str = BASE_RELEASE
    deployed_at: float = field(default_factory=time.time)
    spike_until: float = 0.0

    def deploy(self, release: str, spike_seconds: int = DEPLOY_SPIKE_SECONDS) -> None:
        self.release = release
        self.deployed_at = time.time()
        self.spike_until = self.deployed_at + spike_seconds

    @property
    def in_spike(self) -> bool:
        return time.time() < self.spike_until

    @property
    def seconds_since_deploy(self) -> int:
        return int(time.time() - self.deployed_at)


DEPLOY = DeployState()


def _rel() -> str:
    return DEPLOY.release


# ---------------------------------------------------------------------------
# 예외 타입 — 로그의 exception 필드에 들어갈 이름들
# ---------------------------------------------------------------------------


class DatabaseConnectionError(Exception):
    """SQLAlchemy/psycopg 계열 연결 실패를 흉내 낸다."""


class TokenExpiredError(Exception):
    """JWT 만료."""


class PaymentGatewayTimeout(TimeoutError):
    """외부 결제 API 타임아웃. TimeoutError 를 상속해 예상 오류 이름을 유지한다."""


class ConfigurationError(Exception):
    """배포 직후 설정 누락."""


# ---------------------------------------------------------------------------
# 1. 결제 외부 API 타임아웃 — TimeoutError, 504
# ---------------------------------------------------------------------------


def payment_timeout(request_id: str | None = None) -> dict:
    request_id = request_id or new_request_id()
    gateway = random.choice(_GATEWAYS)
    elapsed_ms = random.randint(30_000, 45_000)

    try:
        raise PaymentGatewayTimeout(
            f"upstream {_UPSTREAM} did not respond within 30000ms"
        )
    except PaymentGatewayTimeout as exc:
        exc_type, stack = format_exception(exc)
        emit(
            "ERROR",
            # 가변값(주문번호·금액·경과시간)을 일부러 메시지에 섞는다.
            # 정규화가 이걸 지우지 못하면 같은 오류가 매번 다른 그룹이 된다.
            f"payment authorization failed: gateway timeout after {elapsed_ms}ms "
            f"(order_id=ORD-{random.randint(100000, 999999)}, "
            f"amount=KRW {random.randint(10, 900) * 1000}) status=504",
            service=PAYMENT_SERVICE,
            release=_rel(),
            request_id=request_id,
            exception=exc_type,
            stacktrace=stack,
            http_status=504,
            upstream=_UPSTREAM,
            gateway=gateway,
            elapsed_ms=elapsed_ms,
            scenario="payment-timeout",
        )
    return {"status": 504, "request_id": request_id, "exception": "PaymentGatewayTimeout"}


# ---------------------------------------------------------------------------
# 2. DB 연결 실패 — DatabaseConnectionError
# ---------------------------------------------------------------------------


def db_connection_failure(request_id: str | None = None) -> dict:
    request_id = request_id or new_request_id()
    pool_size = random.randint(18, 20)

    try:
        raise DatabaseConnectionError(
            "could not connect to server: Connection refused "
            "(host=postgres port=5432 database=aila)"
        )
    except DatabaseConnectionError as exc:
        exc_type, stack = format_exception(exc)
        emit(
            "ERROR",
            f"order lookup failed: DatabaseConnectionError while acquiring connection "
            f"(pool_in_use={pool_size}/20, waited=5001ms) status=500",
            service=ORDER_SERVICE,
            release=_rel(),
            request_id=request_id,
            exception=exc_type,
            stacktrace=stack,
            http_status=500,
            db_host="postgres",
            db_port=5432,
            pool_in_use=pool_size,
            scenario="db-connection",
        )
    return {"status": 500, "request_id": request_id, "exception": "DatabaseConnectionError"}


# ---------------------------------------------------------------------------
# 3. 인증 토큰 만료 — 401, JWT expired
# ---------------------------------------------------------------------------


def auth_token_expired(request_id: str | None = None) -> dict:
    request_id = request_id or new_request_id()
    expired_ago = random.randint(30, 3600)

    try:
        raise TokenExpiredError("Signature has expired: JWT expired")
    except TokenExpiredError as exc:
        exc_type, stack = format_exception(exc)
        emit(
            "WARNING",
            f"authentication rejected: JWT expired {expired_ago}s ago "
            f"(sub=user-{random.randint(1000, 9999)}) status=401",
            service=AUTH_SERVICE,
            release=_rel(),
            request_id=request_id,
            exception=exc_type,
            stacktrace=stack,
            http_status=401,
            token_kind="access_token",
            scenario="auth-expired",
        )
    return {"status": 401, "request_id": request_id, "exception": "TokenExpiredError"}


# ---------------------------------------------------------------------------
# 4. Null 참조 — 스택트레이스
# ---------------------------------------------------------------------------


def _load_profile(user_id: int) -> None:
    """일부러 None 을 돌려주는 저장소 계층."""
    profile = _fetch_profile_row(user_id)
    # profile 이 None 이라 여기서 AttributeError 가 난다.
    _render_display_name(profile)


def _fetch_profile_row(user_id: int) -> None:
    return None


def _render_display_name(profile) -> str:
    # AttributeError: 'NoneType' object has no attribute 'display_name'
    return profile.display_name.strip()


def null_reference(request_id: str | None = None) -> dict:
    """상위 스택 프레임이 여러 개인 진짜 트레이스백을 만든다.

    fingerprint 는 스택 **전체**가 아니라 상위 프레임만 써야 한다는 설계 결정을
    검증하려면, 호출 경로가 여러 단계인 실제 스택이 필요하다.
    """
    request_id = request_id or new_request_id()
    user_id = random.randint(1000, 9999)

    try:
        _load_profile(user_id)
    except (AttributeError, TypeError) as exc:
        exc_type, stack = format_exception(exc)
        emit(
            "ERROR",
            f"profile rendering failed for user_id={user_id}: {exc} status=500",
            service=PAYMENT_SERVICE,
            release=_rel(),
            request_id=request_id,
            exception=exc_type,
            stacktrace=stack,
            http_status=500,
            scenario="null-reference",
        )
        return {"status": 500, "request_id": request_id, "exception": exc_type}

    return {"status": 200, "request_id": request_id, "exception": None}


# ---------------------------------------------------------------------------
# 5. 배포 직후 오류 증가 — release 라벨 변화
# ---------------------------------------------------------------------------


def trigger_deploy(release: str | None = None, spike_seconds: int | None = None) -> dict:
    """새 release 로 '배포'하고 일정 시간 오류율을 올린다."""
    target = release or (
        NEXT_RELEASE if DEPLOY.release != NEXT_RELEASE else BASE_RELEASE
    )
    previous = DEPLOY.release
    DEPLOY.deploy(target, spike_seconds or DEPLOY_SPIKE_SECONDS)

    emit(
        "INFO",
        f"deployment completed: {previous} -> {target}",
        service=PAYMENT_SERVICE,
        release=target,
        request_id=new_request_id(),
        previous_release=previous,
        scenario="post-deploy-spike",
    )
    return {
        "previous_release": previous,
        "release": target,
        "spike_seconds": spike_seconds or DEPLOY_SPIKE_SECONDS,
    }


def post_deploy_error(request_id: str | None = None) -> dict:
    """새 release 에서만 나는 오류. release 라벨로 전후를 가를 수 있어야 한다."""
    request_id = request_id or new_request_id()

    try:
        raise ConfigurationError(
            "required setting PAYMENT_GATEWAY_V2_URL is missing in this environment"
        )
    except ConfigurationError as exc:
        exc_type, stack = format_exception(exc)
        emit(
            "ERROR",
            f"request failed after deploy: ConfigurationError "
            f"(release={_rel()}, since_deploy={DEPLOY.seconds_since_deploy}s) status=500",
            service=PAYMENT_SERVICE,
            release=_rel(),
            request_id=request_id,
            exception=exc_type,
            stacktrace=stack,
            http_status=500,
            scenario="post-deploy-spike",
        )
    return {"status": 500, "request_id": request_id, "exception": "ConfigurationError"}


# ---------------------------------------------------------------------------
# 6. 비밀값 포함 로그 — 마스킹 검증
# ---------------------------------------------------------------------------


def secret_leak(request_id: str | None = None) -> dict:
    """운영 코드가 흔히 저지르는 실수 — 요청 컨텍스트를 통째로 찍는다.

    여기 등장하는 값은 전부 가짜다(위 상수 주석 참조). 이 시나리오의 목적은
    **마스킹 후 페이로드에 원문이 남아 있지 않음을 단언하는 자동 테스트**의 입력을
    만드는 것이다. 눈으로 보는 검증은 반드시 놓친다.
    """
    request_id = request_id or new_request_id()

    emit(
        "ERROR",
        (
            "payment retry failed; dumping request context for debugging -- "
            f"authorization={FAKE_BEARER} "
            f"user_email={FAKE_EMAIL} phone={FAKE_PHONE} card={FAKE_CARD} "
            f"api_key={FAKE_API_KEY} db={FAKE_DB_URL} cookie={FAKE_COOKIE} "
            "status=502"
        ),
        service=PAYMENT_SERVICE,
        release=_rel(),
        request_id=request_id,
        exception="PaymentRetryError",
        stacktrace=(
            'Traceback (most recent call last):\n'
            '  File "/app/payment/retry.py", line 88, in retry_charge\n'
            "    response = client.post(url, headers={'Authorization': "
            f"'{FAKE_BEARER}'}})\n"
            '  File "/app/payment/client.py", line 41, in post\n'
            "    raise PaymentRetryError(f'upstream refused for {contact_email}')\n"
            f"PaymentRetryError: upstream refused for {FAKE_EMAIL_ALT}"
        ),
        http_status=502,
        # 필드 단위로도 새는 경로를 만든다. 메시지 본문만 마스킹하면 여기가 남는다.
        authorization=FAKE_BEARER,
        contact_email=FAKE_EMAIL_ALT,
        scenario="secret-leak",
    )
    return {"status": 502, "request_id": request_id, "exception": "PaymentRetryError"}


# ---------------------------------------------------------------------------
# 정상 로그와 비정형 로그
# ---------------------------------------------------------------------------

_OK_MESSAGES = (
    "payment authorized",
    "order created",
    "token refreshed",
    "webhook delivered",
)


def healthy_traffic(request_id: str | None = None) -> dict:
    """ERROR 필터가 의미를 갖도록 정상 로그도 섞는다."""
    request_id = request_id or new_request_id()
    emit(
        "INFO",
        f"{random.choice(_OK_MESSAGES)} in {random.randint(12, 240)}ms status=200",
        service=random.choice((PAYMENT_SERVICE, ORDER_SERVICE, AUTH_SERVICE)),
        release=_rel(),
        request_id=request_id,
        http_status=200,
        scenario="healthy",
    )
    return {"status": 200, "request_id": request_id, "exception": None}


def _unstructured_lines() -> list[str]:
    """`| json` 이 실패하는 라인들.

    실제 시스템에서 비정형 로그가 섞이는 세 가지 전형을 재현한다 —
    레거시 포맷, 라이브러리가 직접 찍은 경고, 그리고 잘린 JSON.
    잘린 JSON 이 특히 중요하다. 로그 드라이버 버퍼 한계로 흔히 생기는데,
    `| json` 은 이걸 조용히 흘려보내고 후속 필터가 지워버린다.
    """
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    return [
        f"{now} WARN  [{PAYMENT_SERVICE}] connection pool usage 82% "
        f"(release={_rel()}) -- legacy formatter, not JSON",
        f'ts={now} level=error msg="settlement batch lagging" lag_seconds='
        f"{random.randint(30, 600)} component=scheduler",
        "  at com.example.payment.SettlementJob.run(SettlementJob.java:132)",
        # 로그 라인이 중간에 잘린 경우 — JSON 으로 시작하지만 파싱되지 않는다.
        f'{{"timestamp": "{now}", "service": "{PAYMENT_SERVICE}", "level": "ERROR", '
        f'"message": "truncated by log driver buffer at 16k',
    ]


def unstructured_noise() -> dict:
    line = random.choice(_unstructured_lines())
    emit_raw(line)
    return {"status": None, "request_id": None, "exception": None, "raw": True}


# ---------------------------------------------------------------------------
# 레지스트리
# ---------------------------------------------------------------------------

SCENARIOS = {
    "payment-timeout": payment_timeout,
    "db-connection": db_connection_failure,
    "auth-expired": auth_token_expired,
    "null-reference": null_reference,
    "post-deploy-spike": post_deploy_error,
    "secret-leak": secret_leak,
}

SCENARIO_META = {
    "payment-timeout": {
        "expected_error": "TimeoutError / PaymentGatewayTimeout, HTTP 504",
        "verifies": "빈도 급증 감지·LLM 원인 가설",
        "service": PAYMENT_SERVICE,
        "endpoint": "GET /payment/charge",
    },
    "db-connection": {
        "expected_error": "DatabaseConnectionError, HTTP 500",
        "verifies": "오류 그룹화·대응 절차",
        "service": ORDER_SERVICE,
        "endpoint": "GET /orders",
    },
    "auth-expired": {
        "expected_error": "TokenExpiredError / JWT expired, HTTP 401",
        "verifies": "서비스·기간 필터",
        "service": AUTH_SERVICE,
        "endpoint": "GET /auth/me",
    },
    "null-reference": {
        "expected_error": "TypeError/AttributeError with stack trace, HTTP 500",
        "verifies": "스택 프레임 추출",
        "service": PAYMENT_SERVICE,
        "endpoint": "GET /profile",
    },
    "post-deploy-spike": {
        "expected_error": "ConfigurationError, release 라벨 변화",
        "verifies": "배포 전후 비교",
        "service": PAYMENT_SERVICE,
        "endpoint": "POST /admin/deploy 후 자동 증가",
    },
    "secret-leak": {
        "expected_error": "PaymentRetryError + 가짜 Bearer/이메일/카드번호",
        "verifies": "마스킹 (자동 테스트 필수)",
        "service": PAYMENT_SERVICE,
        "endpoint": "GET /debug/dump-context",
    },
}

# 백그라운드 루프의 기본 가중치. 배포 직후 구간에서는 아래 tick() 이 조정한다.
_BASE_WEIGHTS: dict[str, float] = {
    "healthy": 10.0,
    "payment-timeout": 3.0,
    "db-connection": 2.0,
    "auth-expired": 3.0,
    "null-reference": 1.5,
    "secret-leak": 0.6,
    "unstructured": 2.0,
    "post-deploy-spike": 0.0,
}


def pick_and_run() -> str:
    """가중치에 따라 시나리오 하나를 골라 실행하고 이름을 돌려준다."""
    weights = dict(_BASE_WEIGHTS)

    if DEPLOY.in_spike:
        # 배포 직후: 새 release 에서만 나는 오류가 몰리고 전체 오류율이 오른다.
        weights["post-deploy-spike"] = 12.0
        weights["payment-timeout"] = 6.0
        weights["healthy"] = 4.0

    names = list(weights)
    chosen = random.choices(names, weights=[weights[n] for n in names], k=1)[0]

    if chosen == "healthy":
        healthy_traffic()
    elif chosen == "unstructured":
        unstructured_noise()
    else:
        SCENARIOS[chosen]()

    return chosen
