"""정책 CRUD·한도 검증과 정책 실행(query run) 서비스.

Phase 1 담당 트랙: **정책 API**

계약상 제약:
- 기간·라인 수 상한은 **서버에서** 강제한다. UI 제한은 API 직접 호출로 우회된다.
- 쿼리 실행 경로를 한 곳(`execute_query_run`)으로 모은다 — 나중에 "저장된 정책의
  selector 범위를 벗어나지 않는지" 검사를 끼워 넣을 지점이다.
- 처리 순서는 **마스킹 → 정규화 → fingerprint** 로 고정한다. 이 순서는
  `grouping.group_records()` 안에서 지켜지므로 여기서는 순서를 흔들지 않는다.
- `DELETE /policies/{id}` 는 실제 삭제가 아니라 `active=false` 다.

한도 초과 처리 방침 (트랙 결정):
- **기간·라인 수는 422 가 아니라 clamp** 한다. 대시보드에서 "지난 24 시간"을 눌렀을 때
  정책 한도가 1 시간이면 오류로 튕기는 것보다 1 시간치를 보여주는 편이 낫고, 조정
  사실은 `query_runs.warnings` 로 응답에 남으므로 조용히 줄어들지 않는다.
- 반대로 **정책 저장 시점의 한도 값 자체는 422 로 거절**한다. 저장은 사람이 값을
  고칠 수 있는 순간이라 조용히 바꿔주면 설정이 실제와 달라진다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.enums import QueryRunStatus
from app.models import (
    SETTING_DAILY_ANALYSIS_LIMIT,
    SETTING_SAMPLE_RETENTION_DAYS,
    AnalysisPolicy,
    AppSetting,
    ErrorGroup,
    ErrorSample,
    LokiConnection,
    QueryRun,
)
from app.policies import integrations
from app.schemas.api import (
    PolicyCreate,
    PolicyPreviewRequest,
    PolicyPreviewResponse,
    PolicyRead,
    PolicyUpdate,
    QueryRunCreateRequest,
    QueryRunRead,
    SamplePurgeResponse,
)
from app.schemas.logrecord import FetchWarning, LogRecord, TimeRange

#: 그룹당 대표 로그 수의 절대 상한. 프롬프트에 들어가는 양을 정책이 임의로 키우지 못하게 한다
#: (설계: "마스킹된 대표 로그 최대 3 개" — 여유를 두되 무한대는 두지 않는다).
MAX_SAMPLES_PER_GROUP_CAP = get_settings().max_samples_per_group_cap

#: `query_runs.warnings` 에 실리는 경고 코드. 프런트가 문자열이 아니라 코드로 분기한다.
WARN_RANGE_CLAMPED = "range_clamped"
WARN_LIMIT_CLAMPED = "limit_clamped"
WARN_EXCLUDED = "excluded_by_policy"
WARN_TRUNCATED = "limit_reached"

#: 422. `status.HTTP_422_UNPROCESSABLE_ENTITY` 는 최신 starlette 에서 deprecated 이고
#: `HTTP_422_UNPROCESSABLE_CONTENT` 는 구버전에 없어, 숫자를 직접 쓴다.
HTTP_422 = 422

#: 미리보기 응답에 싣는 라인 수 상한. 미리보기는 "무엇이 잡히는가"만 보면 되고,
#: 화면으로 나가는 로그 라인은 적을수록 좋다 (전부 마스킹된 값이라 해도).
MAX_PREVIEW_SAMPLE_LINES = 50

#: `error_samples` 자동 purge 의 마지막 실행 시각을 적어 두는 내부 설정 키.
#: 예약 3 종과 달리 사람이 고치는 값이 아니므로 설정 API 화이트리스트에는 넣지 않는다.
SETTING_SAMPLE_PURGE_LAST_RUN = "sample_retention_last_purge_at"

#: 자동 purge 주기.
PURGE_INTERVAL = timedelta(days=1)

#: `error_groups` 컬럼 폭. 넘치면 PostgreSQL 이 조회 전체를 실패시킨다
#: (SQLite 는 조용히 통과시켜 환경에 따라 결과가 갈린다).
MAX_SERVICE_CHARS = 128
MAX_ENVIRONMENT_CHARS = 64
MAX_ERROR_TYPE_CHARS = 255


# ------------------------------------------------------------------ helpers


def as_utc(value: datetime) -> datetime:
    """naive datetime 은 UTC 로 간주한다 (쿼리 파라미터가 tz 없이 오는 경우)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _dump_warnings(warnings: Iterable[FetchWarning]) -> list[dict]:
    return [warning.model_dump(mode="json") for warning in warnings]


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=HTTP_422, detail=message)


def _clip(value: str | None, limit: int) -> str | None:
    """컬럼 폭에 맞춰 자른다. `None`/빈 값은 그대로 둔다."""
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit]


# ------------------------------------------------------------- 한도 검증


def global_daily_analysis_limit(db: Session) -> int:
    """전역 일일 분석 한도. `app_settings` 행이 없으면 설정 기본값."""
    row = db.get(AppSetting, SETTING_DAILY_ANALYSIS_LIMIT)
    value = row.value if row is not None else None
    if isinstance(value, bool):  # bool 은 int 의 서브클래스라 먼저 걸러낸다
        value = None
    if isinstance(value, int) and value >= 0:
        return value
    return get_settings().default_daily_analysis_limit


def sample_retention_days(db: Session) -> int:
    """`error_samples` 보존 일수. `app_settings` 행이 없으면 설정 기본값."""
    row = db.get(AppSetting, SETTING_SAMPLE_RETENTION_DAYS)
    value = row.value if row is not None else None
    if isinstance(value, bool):  # bool 은 int 의 서브클래스라 먼저 걸러낸다
        value = None
    if isinstance(value, int) and value >= 0:
        return value
    return get_settings().default_sample_retention_days


def _last_purge_at(db: Session) -> datetime | None:
    row = db.get(AppSetting, SETTING_SAMPLE_PURGE_LAST_RUN)
    if row is None or not isinstance(row.value, str):
        return None
    try:
        return as_utc(datetime.fromisoformat(row.value))
    except ValueError:
        return None


def _mark_purged(db: Session, moment: datetime) -> None:
    row = db.get(AppSetting, SETTING_SAMPLE_PURGE_LAST_RUN)
    if row is None:
        row = AppSetting(
            key=SETTING_SAMPLE_PURGE_LAST_RUN,
            description="error_samples 자동 purge 의 마지막 실행 시각 (ISO 8601, 내부용).",
        )
        db.add(row)
    row.value = moment.isoformat()


def purge_expired_samples(db: Session) -> SamplePurgeResponse:
    """보존 기간이 지난 `error_samples` 를 삭제한다.

    설계 문서가 보존 기간을 둔 이유는 하나다 — **마스킹 규칙 강화는 이미 저장된
    샘플에 소급되지 않는다.** 규칙을 고치기 전에 저장된 샘플이 무기한 남으면, 규칙을
    아무리 고쳐도 옛 유출면은 그대로 DB 에 있다. 그래서 `sample_retention_days` 는
    읽는 코드가 반드시 있어야 하는 설정이다.

    `ix_error_samples_created_at` 인덱스를 그대로 탄다.
    """
    days = sample_retention_days(db)
    now = _now()
    if days <= 0:
        # 0 = 보존하지 않음이 아니라 "자동 삭제 끔"으로 읽는다. 데이터를 지우는
        # 동작은 설정 오타 하나로 전부 날아가면 안 되므로 명시적으로 막는다.
        _mark_purged(db, now)
        db.commit()
        return SamplePurgeResponse(deleted=0, retention_days=days, cutoff=None)

    cutoff = now - timedelta(days=days)
    deleted = db.execute(
        delete(ErrorSample).where(ErrorSample.created_at < cutoff)
    ).rowcount
    _mark_purged(db, now)
    db.commit()
    return SamplePurgeResponse(
        deleted=int(deleted or 0), retention_days=days, cutoff=cutoff
    )


def purge_expired_samples_if_due(db: Session) -> SamplePurgeResponse:
    """하루 1 회만 실제로 도는 자동 purge (정책 실행 진입점에서 부른다).

    MVP 에는 스케줄러가 없다 — 설계상 자동 실행을 늘리지 않기로 했으므로, 이미 서버가
    도는 경로 중 가장 자연스러운 곳(정책 실행)에 얹는다. 수동 실행은
    `POST /api/maintenance/purge-samples` 다.
    """
    last = _last_purge_at(db)
    if last is not None and _now() - last < PURGE_INTERVAL:
        return SamplePurgeResponse(
            deleted=0, retention_days=sample_retention_days(db), executed=False
        )
    return purge_expired_samples(db)


def validate_exclusions(patterns: Sequence[str]) -> None:
    """제외 정규식이 컴파일 가능한지 저장 시점에 확인한다.

    조회 시점에 터지면 정책은 저장돼 있는데 실행만 계속 실패하는 상태가 된다.
    """
    broken = []
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            broken.append(f"{pattern!r} ({exc})")
    if broken:
        raise _unprocessable("제외 정규식을 컴파일할 수 없습니다: " + ", ".join(broken))


def validate_limits(
    db: Session,
    *,
    default_range_minutes: int | None,
    max_lines: int | None,
    max_samples_per_group: int | None,
    daily_analysis_limit: int | None,
) -> None:
    """정책 한도가 서버 상한 안에 있는지 확인한다 (하한 `> 0` 은 스키마가 본다)."""
    settings = get_settings()
    problems: list[str] = []

    range_ceiling = settings.max_query_range_minutes
    if default_range_minutes is not None and default_range_minutes > range_ceiling:
        problems.append(
            f"default_range_minutes 는 {range_ceiling} 분을 넘을 수 없습니다 "
            f"(요청: {default_range_minutes})."
        )
    if max_lines is not None and max_lines > settings.max_lines_per_query:
        problems.append(
            f"max_lines 는 {settings.max_lines_per_query} 줄을 넘을 수 없습니다 (요청: {max_lines})."
        )
    if max_samples_per_group is not None and max_samples_per_group > MAX_SAMPLES_PER_GROUP_CAP:
        problems.append(
            f"max_samples_per_group 은 {MAX_SAMPLES_PER_GROUP_CAP} 을 넘을 수 없습니다 "
            f"(요청: {max_samples_per_group})."
        )
    if daily_analysis_limit is not None:
        ceiling = global_daily_analysis_limit(db)
        if daily_analysis_limit > ceiling:
            problems.append(
                f"daily_analysis_limit 은 전역 한도 {ceiling} 회를 넘을 수 없습니다 "
                f"(요청: {daily_analysis_limit})."
            )

    if problems:
        raise _unprocessable(" ".join(problems))


def _require_connection(db: Session, connection_id: int) -> LokiConnection:
    connection = db.get(LokiConnection, connection_id)
    if connection is None:
        raise _unprocessable(f"loki_connection_id={connection_id} 인 연결이 없습니다.")
    return connection


def _require_policy(db: Session, policy_id: int) -> AnalysisPolicy:
    policy = db.get(AnalysisPolicy, policy_id)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"정책 {policy_id} 을(를) 찾을 수 없습니다."
        )
    return policy


def _reject_duplicate_name(db: Session, name: str, *, exclude_id: int | None = None) -> None:
    stmt = select(AnalysisPolicy.id).where(AnalysisPolicy.name == name)
    if exclude_id is not None:
        stmt = stmt.where(AnalysisPolicy.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"정책 이름 '{name}' 은 이미 사용 중입니다."
        )


# ------------------------------------------------------------------ CRUD


def list_policies(db: Session, active: bool | None = None) -> list[PolicyRead]:
    stmt = select(AnalysisPolicy).order_by(AnalysisPolicy.id.asc())
    if active is not None:
        stmt = stmt.where(AnalysisPolicy.active.is_(active))
    return [PolicyRead.model_validate(policy) for policy in db.scalars(stmt).all()]


def get_policy(db: Session, policy_id: int) -> PolicyRead:
    return PolicyRead.model_validate(_require_policy(db, policy_id))


def create_policy(db: Session, payload: PolicyCreate) -> PolicyRead:
    _require_connection(db, payload.loki_connection_id)
    validate_exclusions(payload.exclusions)
    validate_limits(
        db,
        default_range_minutes=payload.default_range_minutes,
        max_lines=payload.max_lines,
        max_samples_per_group=payload.max_samples_per_group,
        daily_analysis_limit=payload.daily_analysis_limit,
    )
    _reject_duplicate_name(db, payload.name)

    policy = AnalysisPolicy(**payload.model_dump())
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return PolicyRead.model_validate(policy)


def update_policy(db: Session, policy_id: int, payload: PolicyUpdate) -> PolicyRead:
    policy = _require_policy(db, policy_id)
    changes = payload.model_dump(exclude_unset=True)

    if "loki_connection_id" in changes and changes["loki_connection_id"] is not None:
        _require_connection(db, changes["loki_connection_id"])
    if changes.get("exclusions") is not None:
        validate_exclusions(changes["exclusions"])
    validate_limits(
        db,
        default_range_minutes=changes.get("default_range_minutes"),
        max_lines=changes.get("max_lines"),
        max_samples_per_group=changes.get("max_samples_per_group"),
        daily_analysis_limit=changes.get("daily_analysis_limit"),
    )
    if changes.get("name") is not None:
        _reject_duplicate_name(db, changes["name"], exclude_id=policy.id)

    #: 명시적 null 을 허용하는 필드 (나머지는 NOT NULL 이라 null 을 무시한다).
    nullable_fields = {"description", "daily_analysis_limit"}
    for field, value in changes.items():
        if value is None and field not in nullable_fields:
            continue
        setattr(policy, field, value)

    db.add(policy)
    db.commit()
    db.refresh(policy)
    return PolicyRead.model_validate(policy)


def deactivate_policy(db: Session, policy_id: int) -> None:
    """실제 삭제가 아니라 비활성화. 정책을 지우면 query_runs·분석 이력이 맥락을 잃는다."""
    policy = _require_policy(db, policy_id)
    policy.active = False
    db.add(policy)
    db.commit()


# --------------------------------------------------------- 실행 한도 clamp


def resolve_range(
    policy: AnalysisPolicy, payload: QueryRunCreateRequest
) -> tuple[datetime, datetime, list[FetchWarning]]:
    """요청 기간을 정책 한도(`default_range_minutes`)와 서버 상한으로 clamp 한다."""
    settings = get_settings()
    allowed_minutes = min(policy.default_range_minutes, settings.max_query_range_minutes)
    warnings: list[FetchWarning] = []

    end = as_utc(payload.range_end) if payload.range_end else _now()
    start = (
        as_utc(payload.range_start)
        if payload.range_start
        else end - timedelta(minutes=allowed_minutes)
    )
    if start >= end:
        raise _unprocessable("range_start 는 range_end 보다 앞이어야 합니다.")

    span = timedelta(minutes=allowed_minutes)
    if end - start > span:
        requested_minutes = int((end - start).total_seconds() // 60)
        start = end - span
        warnings.append(
            FetchWarning(
                code=WARN_RANGE_CLAMPED,
                message=(
                    f"요청 기간 {requested_minutes} 분이 정책 한도 {allowed_minutes} 분으로 "
                    f"조정되었습니다 (range_start={start.isoformat()})."
                ),
                count=requested_minutes,
            )
        )
    return start, end, warnings


def resolve_limit(
    policy: AnalysisPolicy, requested: int | None
) -> tuple[int, list[FetchWarning]]:
    """요청 라인 수를 정책 `max_lines` 와 서버 상한으로 clamp 한다."""
    settings = get_settings()
    ceiling = min(policy.max_lines, settings.max_lines_per_query)
    if requested is None:
        return ceiling, []
    if requested > ceiling:
        return ceiling, [
            FetchWarning(
                code=WARN_LIMIT_CLAMPED,
                message=f"요청 limit {requested} 이 정책 한도 {ceiling} 줄로 조정되었습니다.",
                count=requested,
            )
        ]
    return requested, []


def apply_exclusions(
    records: Sequence[LogRecord], patterns: Sequence[str]
) -> tuple[list[LogRecord], int]:
    """정책 제외 정규식에 걸리는 로그 라인을 결과에서 제거한다.

    `exclusions` 는 **마스킹 추가 패턴이 아니다** — 조회 결과에서 해당 라인을 통째로
    빼는 필터다. 마스킹 추가 패턴은 `group_records(extra_mask_patterns=...)` 쪽이다.
    """
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            # 저장 시점에 검증하지만, 규칙이 바뀐 뒤의 기존 행을 방어한다.
            continue
    if not compiled:
        return list(records), 0
    kept = [
        record
        for record in records
        if not any(matcher.search(record.message) for matcher in compiled)
    ]
    return kept, len(records) - len(kept)


# ------------------------------------------------------------- 정책 실행


def _query_run_read(db: Session, run: QueryRun, group_count: int | None = None) -> QueryRunRead:
    if group_count is None:
        group_count = (
            db.scalar(
                select(func.count(ErrorGroup.id)).where(ErrorGroup.query_run_id == run.id)
            )
            or 0
        )
    return QueryRunRead(
        id=run.id,
        policy_id=run.policy_id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        range_start=run.range_start,
        range_end=run.range_end,
        fetched_count=run.fetched_count,
        dropped_count=run.dropped_count,
        warnings=run.warnings or [],
        error_message=run.error_message,
        group_count=group_count,
    )


def get_query_run(db: Session, run_id: int) -> QueryRunRead:
    run = db.get(QueryRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"조회 이력 {run_id} 을(를) 찾을 수 없습니다."
        )
    return _query_run_read(db, run)


def _persist_groups(db: Session, run_id: int, groups: Sequence[object], max_samples: int) -> int:
    """그룹화 결과를 `error_groups` + `error_samples` 로 저장한다.

    저장하는 로그는 `masked_log` 뿐이다 — 원본은 어느 경로로도 DB 에 들어가지 않는다.
    """
    rule_version = integrations.normalization_rule_version()
    seen: set[str] = set()
    stored = 0
    for grouped in groups:
        fingerprint = grouped.fingerprint
        if fingerprint in seen:
            # (query_run_id, fingerprint) 유니크 제약 방어. 정상 구현이면 발생하지 않는다.
            continue
        seen.add(fingerprint)
        group = ErrorGroup(
            query_run_id=run_id,
            fingerprint=fingerprint,
            # 라벨 값·예외 타입은 로그가 주는 값이라 길이를 보장할 수 없다. 컬럼 폭을
            # 넘기면 PostgreSQL 이 조회 전체를 실패시키므로 저장 시점에 자른다.
            service=_clip(grouped.service, MAX_SERVICE_CHARS),
            environment=_clip(grouped.environment, MAX_ENVIRONMENT_CHARS),
            error_type=_clip(grouped.error_type, MAX_ERROR_TYPE_CHARS),
            normalized_message=grouped.normalized_message,
            count=grouped.count,
            first_seen=grouped.first_seen,
            last_seen=grouped.last_seen,
            labels=dict(grouped.labels or {}),
            top_stack_frame=grouped.top_stack_frame,
            normalization_rule_version=rule_version,
        )
        for sample in list(grouped.samples or [])[:max_samples]:
            group.samples.append(
                ErrorSample(
                    occurred_at=sample.occurred_at,
                    masked_log=sample.masked_log,
                    labels=dict(sample.labels or {}),
                    stacktrace=sample.stacktrace,
                    masking_rule_version=sample.masking_rule_version,
                )
            )
        db.add(group)
        stored += 1
    return stored


def create_query_run(db: Session, policy_id: int, payload: QueryRunCreateRequest) -> QueryRunRead:
    """정책 실행 → 조회 → (마스킹 → 정규화 → fingerprint) → 그룹 저장.

    실패해도 `query_runs` 행은 남는다 (`status=failed` + `error_message`) — 조회가
    왜 비었는지 나중에 확인할 수 있어야 하기 때문이다.

    진입 시 보존 기간이 지난 `error_samples` 를 **하루 1 회** 정리한다 (MVP 에는
    스케줄러가 없다). 실패해도 조회를 막지 않는다.
    """
    try:
        purge_expired_samples_if_due(db)
    except Exception:  # noqa: BLE001 - 정리 실패로 정책 실행을 막지 않는다
        db.rollback()

    policy = _require_policy(db, policy_id)
    if not policy.active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"정책 {policy_id} 은(는) 비활성 상태라 실행할 수 없습니다.",
        )
    connection = db.get(LokiConnection, policy.loki_connection_id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"정책 {policy_id} 의 로그 소스 연결이 없습니다.",
        )
    if not connection.active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"로그 소스 연결 '{connection.name}' 이(가) 비활성 상태입니다.",
        )

    range_start, range_end, warnings = resolve_range(policy, payload)
    limit, limit_warnings = resolve_limit(policy, payload.limit)
    warnings.extend(limit_warnings)

    run = QueryRun(
        policy_id=policy.id,
        started_at=_now(),
        range_start=range_start,
        range_end=range_end,
        status=QueryRunStatus.RUNNING.value,
        warnings=_dump_warnings(warnings),
    )
    db.add(run)
    db.commit()
    run_id = run.id

    try:
        group_count = _execute_query_run(
            db,
            policy=policy,
            connection=connection,
            run=run,
            range_start=range_start,
            range_end=range_end,
            limit=limit,
            warnings=list(warnings),
        )
    except Exception as exc:  # noqa: BLE001 - 어떤 실패든 조회 이력에 남긴다
        db.rollback()
        failed = db.get(QueryRun, run_id)
        failed.status = QueryRunStatus.FAILED.value
        failed.finished_at = _now()
        # 어댑터 예외 메시지에는 요청 URL 이 그대로 실려 온다 — `base_url` 에 박힌
        # 자격증명이 이 컬럼을 타고 DB 와 화면으로 새는 경로다. 저장 전에 마스킹한다.
        failed.error_message = integrations.mask(f"{type(exc).__name__}: {exc}")[:2000]
        failed.warnings = _dump_warnings(warnings)
        db.add(failed)
        db.commit()
        return _query_run_read(db, failed, group_count=0)

    return _query_run_read(db, run, group_count=group_count)


def _execute_query_run(
    db: Session,
    *,
    policy: AnalysisPolicy,
    connection: LokiConnection,
    run: QueryRun,
    range_start: datetime,
    range_end: datetime,
    limit: int,
    warnings: list[FetchWarning],
) -> int:
    """쿼리 실행 경로의 단일 진입점 (selector 범위 검사를 끼워 넣을 지점)."""
    provider = integrations.build_provider(connection)
    result = provider.fetch_logs(
        policy.logql, TimeRange(start=range_start, end=range_end), limit
    )

    records, excluded = apply_exclusions(result.records, policy.exclusions or [])
    if excluded:
        warnings.append(
            FetchWarning(
                code=WARN_EXCLUDED,
                message=f"정책 제외 정규식으로 {excluded} 줄을 결과에서 제외했습니다.",
                count=excluded,
            )
        )
    if result.truncated:
        warnings.append(
            FetchWarning(
                code=WARN_TRUNCATED,
                message="결과가 한도에 걸려 잘렸습니다. 건수 집계에는 metric 쿼리를 쓰세요.",
            )
        )

    # 마스킹 → 정규화 → fingerprint 는 group_records 안에서 이 순서로 일어난다.
    # exclusions 는 위에서 이미 라인 제거로 소비했으므로 마스킹 패턴으로 넘기지 않는다.
    groups = integrations.group_records(
        records, max_samples_per_group=policy.max_samples_per_group, extra_mask_patterns=()
    )

    stored = _persist_groups(db, run.id, groups, policy.max_samples_per_group)

    run.status = QueryRunStatus.SUCCEEDED.value
    run.finished_at = _now()
    run.fetched_count = result.fetched
    run.dropped_count = result.dropped + excluded
    run.warnings = _dump_warnings([*warnings, *result.warnings])
    run.error_message = None
    db.add(run)
    db.commit()
    return stored


# ---------------------------------------------------------------- preview


def preview_policy(db: Session, payload: PolicyPreviewRequest) -> PolicyPreviewResponse:
    """저장 전 실행 결과 미리보기.

    화면에 나가는 `sample_lines` 는 **마스킹을 거친 값**이다 (계약: 화면 표시 전 마스킹).
    """
    connection = _require_connection(db, payload.loki_connection_id)
    if not connection.active:
        raise _unprocessable(f"로그 소스 연결 '{connection.name}' 이(가) 비활성 상태입니다.")
    validate_exclusions(payload.exclusions)

    settings = get_settings()
    range_minutes = min(payload.range_minutes, settings.max_query_range_minutes)
    limit = min(payload.limit, settings.max_lines_per_query)
    end = _now()
    time_range = TimeRange(start=end - timedelta(minutes=range_minutes), end=end)

    warnings: list[FetchWarning] = []
    if range_minutes < payload.range_minutes:
        warnings.append(
            FetchWarning(
                code=WARN_RANGE_CLAMPED,
                message=f"요청 기간이 서버 상한 {range_minutes} 분으로 조정되었습니다.",
            )
        )
    if limit < payload.limit:
        warnings.append(
            FetchWarning(
                code=WARN_LIMIT_CLAMPED,
                message=f"요청 limit 이 서버 상한 {limit} 줄로 조정되었습니다.",
            )
        )

    provider = integrations.build_provider(connection)
    result = provider.fetch_logs(payload.logql, time_range, limit)
    records, excluded = apply_exclusions(result.records, payload.exclusions)
    if excluded:
        warnings.append(
            FetchWarning(
                code=WARN_EXCLUDED,
                message=f"제외 정규식으로 {excluded} 줄을 제외했습니다.",
                count=excluded,
            )
        )

    return PolicyPreviewResponse(
        fetched=result.fetched,
        dropped=result.dropped + excluded,
        truncated=result.truncated,
        warnings=[*warnings, *result.warnings],
        # 집계 수치(fetched/dropped)는 그대로 두고 **화면에 뿌리는 줄만** 자른다.
        sample_lines=[
            integrations.mask(record.message)
            for record in records[:MAX_PREVIEW_SAMPLE_LINES]
        ],
    )


__all__ = [
    "MAX_PREVIEW_SAMPLE_LINES",
    "MAX_SAMPLES_PER_GROUP_CAP",
    "PURGE_INTERVAL",
    "SETTING_SAMPLE_PURGE_LAST_RUN",
    "WARN_EXCLUDED",
    "WARN_LIMIT_CLAMPED",
    "WARN_RANGE_CLAMPED",
    "WARN_TRUNCATED",
    "apply_exclusions",
    "as_utc",
    "create_policy",
    "create_query_run",
    "deactivate_policy",
    "get_policy",
    "get_query_run",
    "global_daily_analysis_limit",
    "list_policies",
    "preview_policy",
    "purge_expired_samples",
    "purge_expired_samples_if_due",
    "resolve_limit",
    "resolve_range",
    "sample_retention_days",
    "update_policy",
    "validate_exclusions",
    "validate_limits",
]
