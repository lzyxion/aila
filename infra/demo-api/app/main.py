"""AILA 데모 API — 의도적으로 오류를 내는 FastAPI 앱.

두 가지 방식으로 로그를 만든다.

  1) **백그라운드 루프** — 컨테이너가 떠 있는 동안 계속 트래픽을 만든다.
     아무것도 하지 않아도 Loki 에 데이터가 쌓여야, "결과가 비었다"는 증상이
     조회 버그인지 수집 실패인지 구분할 수 있다.
  2) **엔드포인트** — 특정 시나리오를 원하는 시점에 원하는 만큼 터뜨린다.
     빈도 급증 감지·배포 전후 비교처럼 시점이 중요한 검증에 쓴다.

주의: 이 앱은 데모 로그 **생산자**다. AILA 백엔드가 읽는 대상은 이 앱이 아니라
Loki 이며, 이 앱과 백엔드 사이에는 직접 연결이 없다.
"""

from __future__ import annotations

import asyncio
import os
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .logging_setup import ENVIRONMENT, SERVICE_NAME, emit, new_request_id
from . import scenarios as sc

AUTO_TRAFFIC = os.getenv("DEMO_AUTO_TRAFFIC", "true").lower() in ("1", "true", "yes")
INTERVAL_SECONDS = float(os.getenv("DEMO_INTERVAL_SECONDS", "5"))
EVENTS_PER_TICK = int(os.getenv("DEMO_EVENTS_PER_TICK", "3"))
MAX_BURST = int(os.getenv("DEMO_MAX_BURST", "500"))


async def _traffic_loop() -> None:
    while True:
        try:
            for _ in range(EVENTS_PER_TICK):
                sc.pick_and_run()
                # 같은 tick 안의 이벤트가 같은 타임스탬프로 뭉치지 않게 흩는다.
                await asyncio.sleep(random.uniform(0.05, 0.3))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 루프는 어떤 경우에도 죽지 않아야 한다
            exc_type, stack = sc.format_exception(exc)
            emit(
                "ERROR",
                f"demo traffic loop iteration failed: {exc}",
                exception=exc_type,
                stacktrace=stack,
                request_id=new_request_id(),
                release=sc.DEPLOY.release,
                scenario="internal",
            )
        await asyncio.sleep(INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    emit(
        "INFO",
        f"demo-api started (auto_traffic={AUTO_TRAFFIC}, "
        f"interval={INTERVAL_SECONDS}s, events_per_tick={EVENTS_PER_TICK})",
        release=sc.DEPLOY.release,
        request_id=new_request_id(),
        scenario="internal",
    )
    task: asyncio.Task | None = None
    if AUTO_TRAFFIC:
        task = asyncio.create_task(_traffic_loop())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        emit(
            "INFO",
            "demo-api stopped",
            release=sc.DEPLOY.release,
            request_id=new_request_id(),
            scenario="internal",
        )


app = FastAPI(
    title="AILA demo-api",
    description="장애 시나리오 6 종을 재현하는 로그 생산자",
    version="1.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------- 공통


def _fail(result: dict, message: str) -> JSONResponse:
    """시나리오 실행 결과를 HTTP 오류 응답으로 변환한다.

    HTTPException 을 쓰지 않는 이유는, 예외 로그를 시나리오 함수가 이미 남겼는데
    FastAPI 가 또 한 번 트레이스백을 찍으면 같은 오류가 두 그룹으로 갈리기 때문이다.
    """
    return JSONResponse(
        status_code=result["status"],
        content={
            "error": result["exception"],
            "message": message,
            "request_id": result["request_id"],
            "release": sc.DEPLOY.release,
        },
    )


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "environment": ENVIRONMENT,
        "release": sc.DEPLOY.release,
        "auto_traffic": AUTO_TRAFFIC,
    }


@app.get("/scenarios")
async def list_scenarios() -> dict:
    return {
        "release": sc.DEPLOY.release,
        "in_deploy_spike": sc.DEPLOY.in_spike,
        "scenarios": sc.SCENARIO_META,
        "burst": "POST /scenarios/{id}/burst?count=N",
    }


# ------------------------------------------------------- 시나리오 엔드포인트


@app.get("/payment/charge")
async def payment_charge() -> JSONResponse:
    """1. 결제 외부 API 타임아웃 — TimeoutError, 504"""
    return _fail(sc.payment_timeout(), "payment gateway timeout")


@app.get("/orders")
async def list_orders() -> JSONResponse:
    """2. DB 연결 실패 — DatabaseConnectionError"""
    return _fail(sc.db_connection_failure(), "database unavailable")


@app.get("/auth/me")
async def auth_me() -> JSONResponse:
    """3. 인증 토큰 만료 — 401, JWT expired"""
    return _fail(sc.auth_token_expired(), "token expired")


@app.get("/profile")
async def profile() -> JSONResponse:
    """4. Null 참조 — 스택트레이스"""
    result = sc.null_reference()
    if result["status"] == 200:
        return JSONResponse(status_code=200, content={"status": "ok"})
    return _fail(result, "profile rendering failed")


@app.post("/admin/deploy")
async def deploy(
    release: str | None = Query(default=None, description="새 release. 생략하면 토글"),
    spike_seconds: int | None = Query(default=None, ge=0, le=3600),
) -> dict:
    """5. 배포 직후 오류 증가 — release 라벨을 바꾸고 오류율을 올린다.

    이후 `spike_seconds` 동안 백그라운드 루프가 ConfigurationError 를 집중 발생시킨다.
    Grafana 의 'release 별 ERROR 추이' 패널에서 계단이 보이면 성공이다.
    """
    return sc.trigger_deploy(release, spike_seconds)


@app.get("/debug/dump-context")
async def dump_context() -> JSONResponse:
    """6. 비밀값 포함 로그 — 마스킹 검증용.

    여기서 찍히는 토큰·이메일·카드번호는 **전부 가짜다**
    (`scenarios.py` 상단 상수 주석 참조). 실제 자격증명을 넣지 말 것.
    """
    return _fail(sc.secret_leak(), "context dumped to logs (fake secrets)")


@app.post("/scenarios/{scenario_id}/burst")
async def burst(
    scenario_id: str,
    count: int = Query(default=20, ge=1),
    delay_ms: int = Query(default=0, ge=0, le=5000),
) -> dict:
    """한 시나리오를 count 번 연속 발생시킨다 (빈도 급증 감지 검증용)."""
    if scenario_id not in sc.SCENARIOS:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown scenario", "known": sorted(sc.SCENARIOS)},
        )
    if count > MAX_BURST:
        raise HTTPException(
            status_code=400,
            detail={"error": "count too large", "max": MAX_BURST},
        )

    func = sc.SCENARIOS[scenario_id]
    for _ in range(count):
        func()
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)

    return {"scenario": scenario_id, "emitted": count, "release": sc.DEPLOY.release}


@app.post("/scenarios/noise")
async def noise(count: int = Query(default=10, ge=1, le=200)) -> dict:
    """비정형(non-JSON) 라인만 count 줄 낸다.

    `{service="payment-api"} | json | __error__="JSONParserErr"` 로 잡히는지,
    그리고 `| json | level="ERROR"` 에서는 사라지는지 확인하는 용도다.
    """
    for _ in range(count):
        sc.unstructured_noise()
    return {"emitted": count, "note": "these lines fail `| json`"}


@app.middleware("http")
async def request_context(request: Request, call_next):
    """모든 요청에 request_id 를 부여하고 접근 로그를 JSON 으로 남긴다.

    uvicorn 자체 access log 는 비정형이라 그대로 두고(파싱 실패 케이스로 쓴다),
    구조화 접근 로그는 여기서 따로 낸다.
    """
    request_id = request.headers.get("x-request-id") or new_request_id()
    response = await call_next(request)
    response.headers["x-request-id"] = request_id

    if request.url.path != "/health":
        emit(
            "INFO" if response.status_code < 400 else "WARNING",
            f"{request.method} {request.url.path} -> {response.status_code}",
            release=sc.DEPLOY.release,
            request_id=request_id,
            http_status=response.status_code,
            scenario="access",
        )
    return response
