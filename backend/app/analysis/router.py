"""`/api/error-groups/{id}/analysis-jobs`, `/api/analysis-jobs/...` 라우터.

Phase 2 담당 트랙: **분석 플로우·usage·보고서**

계약상 제약:
- 분석은 **수동 트리거**만 존재한다. 스케줄·임계치 자동 실행을 추가하지 말 것.
- 분석 시작은 **멱등**이다 — 같은 fingerprint 에 진행 중인 작업이 있으면 새로 만들지 않고
  기존 작업을 `reused=True` 로 반환한다. 비용이 나가는 엔드포인트다.
- `requested_at` 기준 `analysis_job_stale_seconds` 를 넘긴 pending/running 작업은 조회
  시점에 `failed` 로 전이시킨다 (BackgroundTasks 는 프로세스 재시작 시 작업을 잃는다).
- 전역·정책별 일일 분석 횟수 상한을 초과하면 실행을 차단한다 (429).
- 보고서는 저장하지 않고 **요청 시점에 렌더링**한다.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.analysis import report as report_renderer
from app.analysis import service
from app.db import get_db
from app.enums import AnalysisJobStatus
from app.schemas.api import (
    AnalysisJobCreateRequest,
    AnalysisJobCreateResponse,
    AnalysisJobListResponse,
    AnalysisJobRead,
)

TRACK = "LLM 분석"

router = APIRouter(tags=["analysis"])


@router.post(
    "/error-groups/{group_id}/analysis-jobs",
    response_model=AnalysisJobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_analysis_job(
    group_id: int,
    payload: AnalysisJobCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> AnalysisJobCreateResponse:
    """분석 작업 생성 후 백그라운드 실행. 상태는 GET 으로 폴링한다.

    LLM 으로 나가는 것은 마스킹된 대표 로그(최대 3 개)·발생 추이·정책 정보뿐이다.
    """
    return service.create_analysis_job(db, group_id, payload, background_tasks)


@router.get("/analysis-jobs", response_model=AnalysisJobListResponse)
def list_analysis_jobs(
    status_filter: AnalysisJobStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, gt=0, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> AnalysisJobListResponse:
    """분석 작업 목록 (최신순·페이지네이션·상태 필터). 프런트 이력 화면·폴링용."""
    return service.list_analysis_jobs(db, job_status=status_filter, limit=limit, offset=offset)


@router.get("/analysis-jobs/{job_id}", response_model=AnalysisJobRead)
def get_analysis_job(job_id: int, db: Session = Depends(get_db)) -> AnalysisJobRead:
    return service.get_analysis_job(db, job_id)


@router.get(
    "/analysis-jobs/{job_id}/report",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}, "description": "Markdown 보고서"}},
)
def get_analysis_report(job_id: int, db: Session = Depends(get_db)) -> Response:
    """저장된 결과 + 그룹 메타데이터를 Markdown 으로 조립해 반환한다.

    보고서에는 "LLM 이 생성한 원인 가설" 표기와 원본 로그로 돌아갈 조회 조건을 반드시
    포함한다. 저장하지 않으므로 템플릿을 고치면 과거 분석도 새 형식으로 나온다.
    """
    markdown = report_renderer.render_report(db, job_id)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="analysis-job-{job_id}.md"',
        },
    )
