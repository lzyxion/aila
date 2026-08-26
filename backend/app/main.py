"""FastAPI 애플리케이션 진입점.

**Phase 1 공유 파일 — 동결(freeze).** 라우터를 모으는 곳이므로 트랙별로 고치면
충돌한다. 각 트랙은 자기 모듈의 `router.py` 만 수정한다.

MVP 에는 인증이 없다. 배포 대상은 **로컬·데모 환경으로 한정**한다 —
분석 실행은 비용이 나가는 API 라, 데모를 벗어나는 순간 최소한 공유 토큰 인증부터 붙인다.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.analysis import router as analysis_router
from app.app_settings import router as app_settings_router
from app.config import Settings, get_settings
from app.connections import router as connections_router
from app.dashboard import router as dashboard_router
from app.error_groups import router as error_groups_router
from app.llm_connections import router as llm_connections_router
from app.policies import router as policies_router
from app.usage import router as usage_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="AILA — Loki 기반 AI 로그 분석기",
        version=__version__,
        description=(
            "Loki 에 쌓인 오류 로그를 정책으로 조회하고, 유사 오류를 그룹으로 묶어 "
            "**마스킹된 대표 로그만** LLM 에 넘겨 원인 가설·확인 절차·대응 초안을 받는다. "
            "인증이 없으므로 로컬·데모 환경 전용."
        ),
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
        """DB 에 붙지 않는 liveness 체크."""
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
