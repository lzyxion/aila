"""FastAPI 애플리케이션 진입점.

**Phase 1 공유 파일 — 동결(freeze).** 라우터를 모으는 곳이므로 트랙별로 고치면
충돌한다. 각 트랙은 자기 모듈의 `router.py` 만 수정한다.

Phase 5 에서 세 가지가 추가되며 파일이 열렸다 (전부 추가·확장이다).

1. **인증** — `/api/**` 는 전역 의존성(`auth.dependencies.enforce_api_auth`)으로
   보호된다. 예외는 `/api/auth/login`·`/api/auth/logout` 과 `/api` 밖의 경로
   (`/health`, `/docs`, `/openapi.json`)뿐이다. OpenAPI 문서는 로컬 편의상 열어 둔다.
   viewer 계정은 GET 만 할 수 있다 (그 외 메서드 403).
2. **관리자 시드** — 기동 시 `AILA_ADMIN_USERNAME` 계정이 없으면 만든다.
   기본값은 `admin/admin` 이다. **데모 밖으로 나가는 순간 반드시 바꾼다.**
3. **스케줄러** — lifespan 에서 asyncio 백그라운드 루프를 띄운다 (단일 워커 전제).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.analysis import router as analysis_router
from app.app_settings import router as app_settings_router
from app.auth import router as auth_router
from app.auth import service as auth_service
from app.auth.dependencies import enforce_api_auth
from app.config import Settings, get_settings
from app.connections import router as connections_router
from app.dashboard import router as dashboard_router
from app.db import get_session_factory
from app.error_groups import router as error_groups_router
from app.llm_connections import router as llm_connections_router
from app.policies import router as policies_router
from app.scheduler import runner as scheduler_runner
from app.usage import router as usage_router

logger = logging.getLogger(__name__)


def configure_logging(settings: Settings) -> None:
    """`app.*` 로거를 `AILA_LOG_LEVEL` 로 맞춘다.

    uvicorn 은 자기 로거만 설정하고 루트는 WARNING 으로 남긴다 — 그대로 두면
    스케줄러 tick 이 남기는 INFO(실행·스킵 사유)가 **어디에도 보이지 않는다.**
    "한도 초과는 예외 없이 스킵하고 로그로 남긴다"는 계약이 로그가 안 보이면 성립하지
    않으므로, 여기서 핸들러를 붙인다. 이미 붙어 있으면(=운영자가 dictConfig 로 설정한
    경우) 건드리지 않는다.
    """
    app_logger = logging.getLogger("app")
    level = getattr(logging, str(settings.log_level).upper(), logging.INFO)
    app_logger.setLevel(level)
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)-8s %(name)s: %(message)s")
        )
        app_logger.addHandler(handler)
        # 루트로도 올리면 uvicorn 핸들러와 겹쳐 같은 줄이 두 번 찍힌다.
        app_logger.propagate = False


def bootstrap_auth() -> None:
    """관리자 시드 + 만료 세션 정리. DB 가 아직 없어도 앱 기동은 막지 않는다.

    마이그레이션은 사람이 통제한다(`alembic upgrade head`)는 규칙 때문에, 스키마가
    적용되기 전 상태로 앱이 뜨는 경우가 정상 경로에 있다. 그때 여기서 죽으면
    "마이그레이션을 돌리려면 앱이 떠야 하는데 앱이 안 뜬다" 가 된다.
    """
    factory = get_session_factory()
    db = factory()
    try:
        created = auth_service.seed_admin(db)
        if created is not None:
            logger.warning(
                "관리자 계정 '%s' 을(를) 생성했습니다. 기본 비밀번호를 쓰고 있다면 "
                "AILA_ADMIN_PASSWORD 로 반드시 교체하십시오.",
                created.username,
            )
        auth_service.purge_expired_sessions(db)
    except Exception:  # noqa: BLE001 - 기동을 막지 않는다 (마이그레이션 전 기동 허용)
        db.rollback()
        logger.warning(
            "인증 부트스트랩을 건너뜁니다 — users 테이블이 아직 없을 수 있습니다 "
            "(`alembic upgrade head`).",
            exc_info=True,
        )
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    bootstrap_auth()
    task = scheduler_runner.start(get_session_factory())
    try:
        yield
    finally:
        await scheduler_runner.stop(task)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="AILA — Loki 기반 AI 로그 분석기",
        version=__version__,
        description=(
            "Loki 에 쌓인 오류 로그를 정책으로 조회하고, 유사 오류를 그룹으로 묶어 "
            "**마스킹된 대표 로그만** LLM 에 넘겨 원인 가설·확인 절차·대응 초안을 받는다. "
            "`/api/**` 는 세션 쿠키 인증이 필요하다 (`POST /api/auth/login`). "
            "viewer 계정은 GET 만 가능하다."
        ),
        lifespan=lifespan,
        # 전역 의존성 — 새 라우터를 추가하면서 인증을 빼먹어도 열리지 않는다.
        # (라우터마다 Depends 를 붙이는 방식은 기본값이 "열림" 이라 조용히 뚫린다)
        dependencies=[Depends(enforce_api_auth)],
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    prefix = settings.api_prefix
    app.include_router(auth_router.router, prefix=prefix)
    app.include_router(connections_router.router, prefix=prefix)
    app.include_router(llm_connections_router.router, prefix=prefix)
    app.include_router(policies_router.router, prefix=prefix)
    app.include_router(policies_router.query_runs_router, prefix=prefix)
    app.include_router(policies_router.maintenance_router, prefix=prefix)
    app.include_router(error_groups_router.router, prefix=prefix)
    app.include_router(analysis_router.router, prefix=prefix)
    app.include_router(dashboard_router.router, prefix=prefix)
    app.include_router(usage_router.router, prefix=prefix)
    app.include_router(app_settings_router.router, prefix=prefix)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        """DB 에 붙지 않는 liveness 체크. 인증도 필요 없다 (`/api` 밖)."""
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
