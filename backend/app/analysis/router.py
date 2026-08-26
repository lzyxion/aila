"""`/api/error-groups/{id}/analysis-jobs`, `/api/analysis-jobs/...` 라우터 (스켈레톤).

Phase 1 담당 트랙: **LLM 분석**

계약상 제약:
- 분석은 **수동 트리거**만 존재한다. 스케줄·임계치 자동 실행을 추가하지 말 것.
- 분석 시작은 **멱등**이다 — 같은 그룹에 진행 중인 작업이 있으면 새로 만들지 않고
  기존 작업을 `reused=True` 로 반환한다. 비용이 나가는 엔드포인트다.
- `requested_at` 기준 `analysis_job_stale_seconds` 를 넘긴 `running` 작업은 `failed` 로
  간주한다 (BackgroundTasks 는 프로세스 재시작 시 진행 중 작업을 잃는다).
- 전역·정책별 일일 분석 횟수 상한을 초과하면 실행을 차단한다 (429).
- 보고서는 저장하지 않고 **요청 시점에 렌더링**한다.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.api import (
    AnalysisJobCreateRequest,
    AnalysisJobCreateResponse,
    AnalysisJobRead,
)
from app.stub import not_implemented

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
    not_implemented(TRACK)


@router.get("/analysis-jobs/{job_id}", response_model=AnalysisJobRead)
def get_analysis_job(job_id: int, db: Session = Depends(get_db)) -> AnalysisJobRead:
    not_implemented(TRACK)


@router.get(
    "/analysis-jobs/{job_id}/report",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}, "description": "Markdown 보고서"}},
)
def get_analysis_report(job_id: int, db: Session = Depends(get_db)) -> Response:
    """저장된 결과 + 그룹 메타데이터를 Markdown 으로 조립해 반환한다.

    보고서에는 "LLM 이 생성한 원인 가설" 표기와 원본 로그 링크를 반드시 포함한다.
    """
    not_implemented(TRACK)
