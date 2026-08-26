"""분석 작업 생성·실행·조회 서비스.

Phase 2 담당 트랙: **분석 플로우·usage·보고서**

계약상 제약:
- 분석은 **수동 트리거**만 존재한다. 스케줄·임계치 자동 실행을 추가하지 않는다.
- 분석 시작은 **멱등**이다 — 같은 **fingerprint** 에 진행 중인 작업이 있으면 새로 만들지
  않고 기존 작업을 `reused=True` 로 돌려준다. 판정은 그룹 id 가 아니라 fingerprint 기준
  (`error_groups.latest_analysis_by_fingerprint`)이다. 그룹 id 로 판정하면 새 조회마다
  id 가 바뀌어 그대로 중복 과금이 된다.
- 전역·정책별 **일일 분석 횟수 상한**을 서버가 강제한다 (429).
- LLM 응답 Pydantic 검증은 **어댑터 밖 공통 경로에서 한 번만** 한다 (`_execute` 안).
- `requested_at` 기준 `analysis_job_stale_seconds` 를 넘긴 pending/running 은 조회 시점에
  `failed` 로 전이시킨다 — BackgroundTasks 는 프로세스 재시작 시 진행 중 작업을 잃고,
  프런트 폴링은 "영원한 running" 을 스스로 벗어날 수 없다.
- 프롬프트로 나가는 것은 마스킹된 대표 로그(최대 3 개)와 그룹 메타데이터뿐이며,
  전송 직전에 **한 번 더** 마스킹한다 (`prompt.build_prompt`).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, tzinfo

from fastapi import BackgroundTasks, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.analysis import integrations, pricing
from app.analysis.prompt import build_prompt, context_from_group
from app.config import get_settings
from app.enums import ACTIVE_JOB_STATUSES, AnalysisJobStatus, UsageStatus
from app.error_groups.service import active_analysis_by_fingerprint, group_trend
from app.models import (
    SETTING_DAILY_ANALYSIS_LIMIT,
    AnalysisJob,
    AnalysisPolicy,
    AnalysisResult,
    AnalysisUsageRecord,
    AppSetting,
    ErrorGroup,
    ErrorSample,
    LLMConnection,
    QueryRun,
)
from app.policies.service import (
    analysis_timezone,
    analysis_timezone_name,
    global_daily_analysis_limit,
)
from app.schemas.analysis import AnalysisResultSchema, parse_analysis_result
from app.schemas.api import (
    AnalysisJobCreateRequest,
    AnalysisJobCreateResponse,
    AnalysisJobListItem,
    AnalysisJobListResponse,
    AnalysisJobRead,
    UsageRecordRead,
)

#: 422. `status.HTTP_422_UNPROCESSABLE_ENTITY` 는 최신 starlette 에서 deprecated 이고
#: `HTTP_422_UNPROCESSABLE_CONTENT` 는 구버전에 없어, 숫자를 직접 쓴다.
HTTP_422 = 422

#: stale 전이 시 남기는 사유. 프런트가 문자열이 아니라 상태로 분기하지만, 화면에 뜬다.
STALE_ERROR_MESSAGE = "분석 작업이 제한 시간 안에 끝나지 않아 실패로 처리했습니다 (stale)."

#: 저장하는 오류 메시지 길이 상한 (`analysis_jobs.error_message` 는 Text 지만 길이를 문다).
MAX_ERROR_MESSAGE_CHARS = 2000

#: DB 에는 문자열이 들어 있고 `AnalysisJobStatus` 는 Enum 이라 해시가 달라, 값 집합으로 비교한다.
ACTIVE_STATUS_VALUES: frozenset[str] = frozenset(
    job_status.value for job_status in ACTIVE_JOB_STATUSES
)


class AnalysisFailure(RuntimeError):
    """실행 경로에서 "이 작업은 실패다"를 표현하는 내부 예외."""


# ------------------------------------------------------------------ helpers


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 는 tz 를 버리고 돌려준다 — naive 는 UTC 로 간주한다."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _start_of_day(moment: datetime, tz: tzinfo) -> datetime:
    """`tz` 기준 오늘 00:00 을 **UTC** 로 돌려준다.

    `requested_at` 은 UTC 로 저장되므로 비교값도 UTC 여야 한다. 로컬 자정을 먼저
    구한 뒤 UTC 로 변환하는 순서가 중요하다 — UTC 자정을 로컬로 옮기면 KST 기준
    오전 9 시가 되어 "하루" 가 9 시간 어긋난다 (1 차 피드백에서 나온 그 증상).
    """
    local = moment.astimezone(tz)
    local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(UTC)


def _short(text: object) -> str:
    """저장용 오류 텍스트 공통 처리 — 마스킹 후 길이 제한.

    LLM base_url 에 내장된 자격증명(`https://user:pw@host`)이 SDK 오류 메시지에
    실려 `analysis_jobs.error_message` / `failure_reason` 으로 평문 저장되는 것을
    막는다 (query_runs.error_message 와 같은 처리).
    """
    return integrations.mask(str(text))[:MAX_ERROR_MESSAGE_CHARS]


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=HTTP_422, detail=message)


def session_factory_for(db: Session) -> sessionmaker[Session]:
    """요청 세션과 같은 engine 을 쓰는 **새 세션** 팩토리.

    FastAPI 0.106+ 는 yield 의존성을 BackgroundTasks 보다 **먼저** 정리하므로, 요청
    세션을 백그라운드에서 계속 쓰면 닫힌 세션을 만진다. engine 만 물려받아 작업 안에서
    세션을 새로 연다 (테스트의 in-memory SQLite 도 같은 engine 이라 그대로 동작한다).
    """
    return sessionmaker(
        bind=db.get_bind(), autoflush=False, autocommit=False, expire_on_commit=False
    )


# ------------------------------------------------------------------- stale


def sweep_stale_jobs(db: Session, jobs: Sequence[AnalysisJob]) -> int:
    """`requested_at` 이 stale 임계를 넘긴 pending/running 을 failed 로 전이·저장한다.

    조회(단건·목록) 경로에서 호출한다. 폴링하는 프런트가 이 규칙에 의존한다.
    """
    settings = get_settings()
    cutoff = _now() - timedelta(seconds=settings.analysis_job_stale_seconds)
    changed = 0
    for job in jobs:
        if job.status not in ACTIVE_STATUS_VALUES:
            continue
        requested_at = _as_utc(job.requested_at)
        if requested_at is None or requested_at > cutoff:
            continue
        job.status = AnalysisJobStatus.FAILED.value
        job.completed_at = _now()
        job.error_message = STALE_ERROR_MESSAGE
        db.add(job)
        changed += 1
    if changed:
        db.commit()
    return changed


# ------------------------------------------------------------------ 읽기


def _result_of(db: Session, job: AnalysisJob) -> AnalysisResult | None:
    return db.scalar(select(AnalysisResult).where(AnalysisResult.analysis_job_id == job.id))


def _usage_of(db: Session, job: AnalysisJob) -> AnalysisUsageRecord | None:
    return db.scalar(
        select(AnalysisUsageRecord).where(AnalysisUsageRecord.analysis_job_id == job.id)
    )


def _job_read(db: Session, job: AnalysisJob) -> AnalysisJobRead:
    result = _result_of(db, job)
    usage = _usage_of(db, job)
    return AnalysisJobRead(
        id=job.id,
        error_group_id=job.error_group_id,
        llm_connection_id=job.llm_connection_id,
        fingerprint=job.fingerprint,
        status=job.status,
        provider=job.provider,
        model=job.model,
        prompt_version=job.prompt_version,
        requested_at=job.requested_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        result=(
            AnalysisResultSchema.model_validate(result.result_json) if result is not None else None
        ),
        usage=UsageRecordRead.model_validate(usage) if usage is not None else None,
    )


def get_analysis_job(db: Session, job_id: int) -> AnalysisJobRead:
    job = db.get(AnalysisJob, job_id)
    if job is None:
        raise _not_found(f"분석 작업 {job_id} 을(를) 찾을 수 없습니다.")
    sweep_stale_jobs(db, [job])
    return _job_read(db, job)


def list_analysis_jobs(
    db: Session,
    *,
    job_status: AnalysisJobStatus | None = None,
    limit: int = 20,
    offset: int = 0,
) -> AnalysisJobListResponse:
    """최신순 목록 (프런트 폴링·이력 화면). stale 전이는 여기서도 적용한다."""
    # 필터 이전에 전이시켜야 "running 필터에 stale 이 계속 남는" 상태가 생기지 않는다.
    active = db.scalars(
        select(AnalysisJob).where(AnalysisJob.status.in_(sorted(ACTIVE_STATUS_VALUES)))
    ).all()
    sweep_stale_jobs(db, list(active))

    conditions = []
    if job_status is not None:
        conditions.append(AnalysisJob.status == job_status.value)

    total = db.scalar(select(func.count(AnalysisJob.id)).where(*conditions)) or 0
    rows = db.execute(
        select(AnalysisJob, AnalysisResult, ErrorGroup)
        .outerjoin(AnalysisResult, AnalysisResult.analysis_job_id == AnalysisJob.id)
        .outerjoin(ErrorGroup, ErrorGroup.id == AnalysisJob.error_group_id)
        .where(*conditions)
        .order_by(AnalysisJob.requested_at.desc(), AnalysisJob.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    items = [
        AnalysisJobListItem(
            id=job.id,
            status=job.status,
            provider=job.provider,
            model=job.model,
            prompt_version=job.prompt_version,
            requested_at=job.requested_at,
            completed_at=job.completed_at,
            severity=result.severity if result is not None else None,
            summary=result.summary if result is not None else None,
            error_group_id=job.error_group_id,
            fingerprint=job.fingerprint,
            llm_connection_id=job.llm_connection_id,
            service=group.service if group is not None else None,
            environment=group.environment if group is not None else None,
            error_type=group.error_type if group is not None else None,
            normalized_message=group.normalized_message if group is not None else None,
            error_message=job.error_message,
        )
        for job, result, group in rows
    ]
    return AnalysisJobListResponse(total=total, limit=limit, offset=offset, items=items)


# ------------------------------------------------------------------ 생성


def _require_group(db: Session, group_id: int) -> ErrorGroup:
    group = db.get(ErrorGroup, group_id)
    if group is None:
        raise _not_found(f"오류 그룹 {group_id} 을(를) 찾을 수 없습니다.")
    return group


def policy_of_group(db: Session, group: ErrorGroup) -> AnalysisPolicy | None:
    run = db.get(QueryRun, group.query_run_id)
    if run is None:
        return None
    return db.get(AnalysisPolicy, run.policy_id)


def _resolve_connection(db: Session, connection_id: int | None) -> LLMConnection:
    """요청 연결 → 없으면 기본 연결. 둘 다 없으면 422 (분석은 시작조차 못 한다)."""
    if connection_id is not None:
        connection = db.get(LLMConnection, connection_id)
        if connection is None:
            raise _unprocessable(f"llm_connection_id={connection_id} 인 LLM 연결이 없습니다.")
        if not connection.active:
            raise _unprocessable(f"LLM 연결 '{connection.name}' 이(가) 비활성 상태입니다.")
        return connection

    default = db.scalar(
        select(LLMConnection)
        .where(LLMConnection.is_default.is_(True), LLMConnection.active.is_(True))
        .order_by(LLMConnection.id.asc())
    )
    if default is None:
        raise _unprocessable(
            "기본 LLM 연결이 없습니다. llm_connection_id 를 지정하거나 기본 연결을 설정하세요."
        )
    return default


def daily_usage(db: Session, policy: AnalysisPolicy | None) -> tuple[int, int]:
    """(오늘 전역 분석 작업 수, 오늘 이 정책의 분석 작업 수).

    "오늘" 의 경계는 `app_settings.timezone` (기본 `Asia/Seoul`) 의 로컬 자정이다.
    UTC 자정 기준이면 한국에서는 업무 시작 시각인 오전 9 시에 카운터가 리셋돼
    "어제 저녁에 쓴 분량이 아침까지 남아 있다" 는 상태가 된다.
    """
    today = _start_of_day(_now(), analysis_timezone(db))
    global_used = (
        db.scalar(select(func.count(AnalysisJob.id)).where(AnalysisJob.requested_at >= today)) or 0
    )
    if policy is None:
        return global_used, 0
    policy_used = (
        db.scalar(
            select(func.count(AnalysisJob.id))
            .join(ErrorGroup, ErrorGroup.id == AnalysisJob.error_group_id)
            .join(QueryRun, QueryRun.id == ErrorGroup.query_run_id)
            .where(QueryRun.policy_id == policy.id, AnalysisJob.requested_at >= today)
        )
        or 0
    )
    return global_used, policy_used


def _lock_for_limit_check(db: Session) -> None:
    """한도 검사와 작업 삽입 사이에 다른 트랜잭션이 끼어들지 못하게 직렬화한다.

    >>> 어디까지 막아 주는가 (한계를 분명히 해 둔다) <<<
    - **PostgreSQL**: `app_settings.daily_analysis_limit` 행을 `SELECT ... FOR UPDATE`
      로 잠근다. 같은 DB 를 보는 프로세스가 몇 개든, count + insert 가 이 잠금 안에서
      직렬화되므로 한도는 정확히 지켜진다. 단 **행이 있어야** 잠글 것이 있다 —
      revision 0002 가 기본값을 시드하는 이유가 이것이다. 행이 없으면 잠금 없이 진행한다
      (그 경우 아래 SQLite 와 같은 수준의 방어만 남는다).
    - **SQLite**: 쓰기가 단일 writer 로 직렬화되므로 한 프로세스 안에서는 사실상 안전하다.
      명시적 잠금은 걸지 않는다.
    - 어느 쪽이든 이 함수는 **한도만** 지킨다. 같은 fingerprint 의 중복 실행은 잠금이
      아니라 `analysis_jobs` 의 부분 유니크 인덱스(= DB 제약)가 막는다.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        select(AppSetting.key)
        .where(AppSetting.key == SETTING_DAILY_ANALYSIS_LIMIT)
        .with_for_update()
    )


def _enforce_daily_limits(db: Session, policy: AnalysisPolicy | None) -> None:
    """전역·정책별 일일 한도. 사용량 대시보드는 사후 확인일 뿐이라 여기서 막아야 한다."""
    global_used, policy_used = daily_usage(db, policy)
    global_limit = global_daily_analysis_limit(db)
    # 언제 풀리는지가 안 보이면 "왜 막혔는지" 를 화면에서 알 수 없다 — 기준 타임존을 싣는다.
    tz_name = analysis_timezone_name(db)
    if global_used >= global_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"오늘 전역 일일 분석 한도 {global_limit} 회를 모두 사용했습니다 "
                f"(사용 {global_used} 회). {tz_name} 자정에 리셋됩니다."
            ),
        )
    if policy is not None and policy.daily_analysis_limit is not None:
        if policy_used >= policy.daily_analysis_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"정책 '{policy.name}' 의 일일 분석 한도 {policy.daily_analysis_limit} 회를 "
                    f"모두 사용했습니다 (사용 {policy_used} 회). "
                    f"{tz_name} 자정에 리셋됩니다."
                ),
            )


def _active_job_for(db: Session, fingerprint: str) -> AnalysisJob | None:
    """같은 fingerprint 로 **아직 돌고 있는** 작업 (stale 전이를 먼저 적용한다).

    "최신 1 건" 이 아니라 active 전용으로 조회하는 것이 핵심이다. 최신 1 건만 보면
    실패한 작업이 뒤에 하나 끼어드는 순간 아직 돌고 있는 작업을 놓치고, 그대로
    두 번째 LLM 호출이 나간다 (= 중복 과금).
    """
    active = active_analysis_by_fingerprint(db, [fingerprint])
    candidate = active.get(fingerprint)
    if candidate is None:
        return None
    sweep_stale_jobs(db, [candidate])
    if candidate.status not in ACTIVE_STATUS_VALUES:
        # stale 로 실패 처리했다 — 그 뒤에 남은 active 가 또 있는지 다시 본다.
        return active_analysis_by_fingerprint(db, [fingerprint]).get(fingerprint)
    return candidate


def create_analysis_job(
    db: Session,
    group_id: int,
    payload: AnalysisJobCreateRequest,
    background_tasks: BackgroundTasks | None = None,
) -> AnalysisJobCreateResponse:
    """분석 작업 생성 → BackgroundTasks 로 실행 예약. 상태는 GET 으로 폴링한다.

    >>> 동시 요청 <<<
    두 사람이 같은 버튼을 동시에 누르면 "체크 -> 삽입" 사이가 경합 구간이 된다.
    응용 레벨 검사만으로는 그 창을 닫을 수 없으므로, 최종 방어선은 DB 제약이다 —
    `analysis_jobs(fingerprint) WHERE status IN ('pending','running')` 부분 유니크
    인덱스가 두 번째 삽입을 `IntegrityError` 로 튕기고, 여기서 그걸 받아 기존 작업을
    `reused=True` 로 돌려준다. 일일 한도는 `_lock_for_limit_check` 가 최선을 다해
    직렬화한다 (한계는 그 함수 docstring 참고).
    """
    group = _require_group(db, group_id)
    policy = policy_of_group(db, group)

    if policy is not None and not policy.allow_ai_analysis:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"정책 '{policy.name}' 은(는) AI 분석을 허용하지 않습니다.",
        )

    # 멱등: fingerprint 기준으로 진행 중인 작업이 있으면 그대로 돌려준다 (중복 과금 차단).
    existing = _active_job_for(db, group.fingerprint)
    if existing is not None:
        return AnalysisJobCreateResponse(**_job_read(db, existing).model_dump(), reused=True)

    # 한도 검사와 삽입을 같은 트랜잭션 안에 묶는다 (그 사이의 조회는 읽기뿐이다).
    _lock_for_limit_check(db)
    _enforce_daily_limits(db, policy)
    connection = _resolve_connection(db, payload.llm_connection_id)

    job = AnalysisJob(
        error_group_id=group.id,
        llm_connection_id=connection.id,
        fingerprint=group.fingerprint,
        status=AnalysisJobStatus.PENDING.value,
        # provider·model 은 연결에서 **값으로 복사**한다 — 연결 설정이 바뀌어도 이력은
        # 실제 사용한 모델을 유지해야 한다.
        provider=connection.provider,
        model=connection.model,
        prompt_version=get_settings().prompt_version,
        requested_at=_now(),
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        # 경합에서 졌다 — 부분 유니크 인덱스가 막아 준 것이다. 이긴 쪽 작업을 돌려준다.
        db.rollback()
        winner = _active_job_for(db, group.fingerprint)
        if winner is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="같은 오류에 대한 분석 작업이 동시에 생성되어 요청을 처리하지 못했습니다.",
            ) from None
        return AnalysisJobCreateResponse(**_job_read(db, winner).model_dump(), reused=True)

    db.refresh(job)

    if background_tasks is not None:
        background_tasks.add_task(run_analysis_job, session_factory_for(db), job.id)

    return AnalysisJobCreateResponse(**_job_read(db, job).model_dump(), reused=False)


# ------------------------------------------------------------------ 실행


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _record_usage(
    db: Session,
    job: AnalysisJob,
    *,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int | None,
    usage_status: UsageStatus,
    failure_reason: str | None = None,
) -> None:
    estimated_cost, snapshot = pricing.price(db, job.model, input_tokens, output_tokens)
    db.add(
        AnalysisUsageRecord(
            analysis_job_id=job.id,
            provider=job.provider,
            model=job.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            pricing_snapshot=snapshot,
            latency_ms=latency_ms,
            status=usage_status.value,
            failure_reason=failure_reason,
        )
    )


def _finish_if_running(
    db: Session, job_id: int, *, job_status: AnalysisJobStatus, error_message: str | None
) -> bool:
    """`status='running'` 일 때만 종료 상태로 갱신한다.

    조건 없이 덮어쓰면, 실행이 stale 임계를 넘겨 sweep 이 `failed` 로 바꿔 둔 작업을
    뒤늦게 끝난 백그라운드 태스크가 `succeeded` 로 **되살린다.** 프런트는 이미 실패를
    보여줬는데 새로고침하면 성공으로 바뀌어 있는 상태가 되고, stale 규칙 자체가 의미를
    잃는다. 갱신하지 못했으면(`False`) 사용량 기록만 남기고 상태는 건드리지 않는다 —
    토큰은 실제로 나갔으므로 usage 는 지운다고 될 일이 아니다.
    """
    result = db.execute(
        update(AnalysisJob)
        .where(
            AnalysisJob.id == job_id,
            AnalysisJob.status == AnalysisJobStatus.RUNNING.value,
        )
        .values(
            status=job_status.value,
            completed_at=_now(),
            error_message=_short(error_message) if error_message else None,
        )
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)


def _fail(
    db: Session,
    job: AnalysisJob,
    message: str,
    *,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int | None,
) -> None:
    _finish_if_running(
        db, job.id, job_status=AnalysisJobStatus.FAILED, error_message=message
    )
    _record_usage(
        db,
        job,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        usage_status=UsageStatus.FAILED,
        failure_reason=_short(message),
    )
    db.commit()
    db.expire(job)


def _prompt_samples(db: Session, group: ErrorGroup) -> list[ErrorSample]:
    """프롬프트에 실을 대표 로그. 최신 것부터 최대 `prompt_max_samples` 개."""
    limit = max(0, get_settings().prompt_max_samples)
    rows = db.scalars(
        select(ErrorSample)
        .where(ErrorSample.error_group_id == group.id)
        .order_by(ErrorSample.occurred_at.desc(), ErrorSample.id.desc())
        .limit(limit)
    ).all()
    # 화면·프롬프트 모두 시간순이 읽기 쉽다.
    return sorted(rows, key=lambda sample: (sample.occurred_at, sample.id))


def _prompt_trend(db: Session, group: ErrorGroup) -> list[tuple[datetime, float]]:
    """프롬프트 "최근 추이" 항목에 실을 (시각, 건수) 목록.

    그룹 상세 화면이 쓰는 것과 **같은 metric 쿼리**다. 추이 조회가 실패해도 분석은
    계속한다 — 프롬프트 고정 목록에서 추이는 "있으면" 싣는 선택 항목이다.
    """
    try:
        points, _ = group_trend(db, group)
    except Exception:  # noqa: BLE001 - 추이 실패로 분석을 죽이지 않는다
        return []
    return [(point.timestamp, point.value) for point in points]


def _execute(db: Session, job_id: int) -> None:
    job = db.get(AnalysisJob, job_id)
    if job is None or job.status != AnalysisJobStatus.PENDING.value:
        return  # 이미 다른 경로가 집어간 작업 (재실행하면 중복 과금이다).

    job.status = AnalysisJobStatus.RUNNING.value
    job.started_at = _now()
    db.add(job)
    db.commit()

    started = time.perf_counter()
    input_tokens = 0
    output_tokens = 0
    try:
        group = db.get(ErrorGroup, job.error_group_id)
        if group is None:
            raise AnalysisFailure("오류 그룹이 삭제되어 분석할 대상이 없습니다.")
        connection = (
            db.get(LLMConnection, job.llm_connection_id)
            if job.llm_connection_id is not None
            else None
        )
        if connection is None:
            raise AnalysisFailure("LLM 연결을 찾을 수 없습니다 (삭제되었을 수 있습니다).")

        prompt = build_prompt(
            context_from_group(
                group, _prompt_samples(db, group), trend=_prompt_trend(db, group)
            ),
            prompt_version=job.prompt_version,
        )
        provider = integrations.build_llm_provider(connection)
        raw, input_tokens, output_tokens = provider.analyze(prompt)
        latency_ms = _elapsed_ms(started)

        # 검증은 어댑터 밖 **공통 경로에서 한 번만**.
        try:
            parsed = parse_analysis_result(raw)
        except ValidationError as exc:
            raise AnalysisFailure(f"LLM 응답이 분석 결과 스키마와 맞지 않습니다: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - 어떤 실패든 작업 이력에 남긴다
        db.rollback()
        job = db.get(AnalysisJob, job_id)
        message = str(exc) if isinstance(exc, AnalysisFailure) else f"{type(exc).__name__}: {exc}"
        _fail(
            db,
            job,
            message,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=_elapsed_ms(started),
        )
        return

    _record_usage(
        db,
        job,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        usage_status=UsageStatus.SUCCEEDED,
    )
    # stale sweep 이 이미 failed 로 바꿔 둔 작업이면 되살리지 않는다.
    if _finish_if_running(
        db, job.id, job_status=AnalysisJobStatus.SUCCEEDED, error_message=None
    ):
        db.add(
            AnalysisResult(
                analysis_job_id=job.id,
                result_json=parsed.model_dump(mode="json"),
                # 목록 표시용 비정규화 컬럼 (result_json 의 동명 필드와 같은 값).
                summary=parsed.summary,
                severity=parsed.severity.value,
            )
        )
    db.commit()
    db.expire(job)


def run_analysis_job(factory: sessionmaker[Session], job_id: int) -> None:
    """BackgroundTasks 진입점. 요청 세션이 아니라 **새 세션**에서 돈다."""
    db = factory()
    try:
        _execute(db, job_id)
    finally:
        db.close()


__all__ = [
    "STALE_ERROR_MESSAGE",
    "AnalysisFailure",
    "create_analysis_job",
    "daily_usage",
    "get_analysis_job",
    "list_analysis_jobs",
    "policy_of_group",
    "run_analysis_job",
    "session_factory_for",
    "sweep_stale_jobs",
]
