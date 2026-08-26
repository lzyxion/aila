"""`/api/loki-connections` 라우터.

Phase 1 담당 트랙: **Loki 어댑터**

계약상 제약 두 가지를 여기서 지킨다.

- secret 은 `app.crypto.encrypt()` 결과만 저장하고, **어떤 응답에도 평문을 싣지 않는다.**
  존재 여부(`has_secret`)만 노출한다.
- `DELETE` 는 실제 삭제가 아니라 `active=false` 비활성화다 — 정책이 참조 중일 수 있다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crypto import DecryptionError, EncryptionKeyMissingError, encrypt
from app.db import get_db
from app.enums import AuthType, SourceType
from app.loki.factory import build_provider
from app.loki.provider import LokiProvider
from app.models import LokiConnection
from app.providers.logsource import LogSourceError, LogSourceProvider
from app.schemas.api import (
    ConnectionTestResponse,
    LabelValuesResponse,
    LokiConnectionCreate,
    LokiConnectionRead,
    LokiConnectionTestRequest,
    LokiConnectionUpdate,
)

TRACK = "Loki 어댑터"

router = APIRouter(prefix="/loki-connections", tags=["loki-connections"])


# ------------------------------------------------------------------ 내부 헬퍼


def _to_read(connection: LokiConnection) -> LokiConnectionRead:
    """ORM 행 -> 응답 모델. `encrypted_secret` 은 존재 여부로만 나간다."""
    return LokiConnectionRead(
        id=connection.id,
        name=connection.name,
        source_type=SourceType(connection.source_type),
        base_url=connection.base_url,
        auth_type=AuthType(connection.auth_type),
        label_mapping=dict(connection.label_mapping or {}),
        active=connection.active,
        has_secret=bool(connection.encrypted_secret),
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _get_or_404(db: Session, connection_id: int) -> LokiConnection:
    connection = db.get(LokiConnection, connection_id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"연결을 찾을 수 없습니다 (id={connection_id}).",
        )
    return connection


def _encrypt_secret(secret: str | None) -> str | None:
    if secret is None or secret == "":
        return None
    try:
        return encrypt(secret)
    except EncryptionKeyMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


def _name_taken(db: Session, name: str, *, exclude_id: int | None = None) -> bool:
    statement = select(LokiConnection.id).where(LokiConnection.name == name)
    if exclude_id is not None:
        statement = statement.where(LokiConnection.id != exclude_id)
    return db.execute(statement).first() is not None


def _provider_for(connection: LokiConnection) -> LogSourceProvider:
    """저장된 연결로 프로바이더를 만든다. 복호화·source_type 오류는 400 으로 바꾼다."""
    try:
        return build_provider(connection)
    except DecryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "저장된 secret 을 복호화할 수 없습니다 "
                "(AILA_ENCRYPTION_KEY 불일치). secret 을 다시 저장하세요."
            ),
        ) from exc
    except EncryptionKeyMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# --------------------------------------------------------------------- CRUD


@router.get("", response_model=list[LokiConnectionRead])
def list_loki_connections(db: Session = Depends(get_db)) -> list[LokiConnectionRead]:
    connections = db.execute(select(LokiConnection).order_by(LokiConnection.id)).scalars().all()
    return [_to_read(connection) for connection in connections]


@router.post("", response_model=LokiConnectionRead, status_code=status.HTTP_201_CREATED)
def create_loki_connection(
    payload: LokiConnectionCreate, db: Session = Depends(get_db)
) -> LokiConnectionRead:
    """secret 은 `app.crypto.encrypt()` 로 암호화해 저장한다. 평문 저장 금지."""
    if _name_taken(db, payload.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 같은 이름의 연결이 있습니다: {payload.name!r}",
        )

    connection = LokiConnection(
        name=payload.name,
        source_type=SourceType(payload.source_type).value,
        base_url=payload.base_url,
        auth_type=AuthType(payload.auth_type).value,
        encrypted_secret=_encrypt_secret(payload.secret),
        label_mapping=dict(payload.label_mapping or {}),
        active=payload.active,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return _to_read(connection)


@router.post("/test", response_model=ConnectionTestResponse)
def test_loki_connection(
    payload: LokiConnectionTestRequest, db: Session = Depends(get_db)
) -> ConnectionTestResponse:
    """저장된 연결 또는 미저장 입력값으로 연결·인증을 테스트한다.

    `connection_id` 를 주면 저장된 값으로 만들고, 함께 넘어온 `base_url`/`secret` 은
    덮어쓴다 — "키를 바꿔 보고 저장" 흐름을 저장 없이 확인할 수 있어야 한다.
    """
    provider: LogSourceProvider
    if payload.connection_id is not None:
        connection = _get_or_404(db, payload.connection_id)
        stored = _provider_for(connection)
        if not isinstance(stored, LokiProvider):  # pragma: no cover - MVP 는 Loki 뿐이다
            provider = stored
        else:
            override_secret = bool(payload.secret)
            provider = LokiProvider(
                base_url=payload.base_url or stored.base_url,
                auth_type=payload.auth_type.value if override_secret else stored.auth_type,
                secret=payload.secret if override_secret else stored.secret,
                label_mapping=dict(connection.label_mapping or {}),
                timeout_seconds=stored.timeout_seconds,
            )
    else:
        if not payload.base_url:
            raise HTTPException(
                status_code=422,
                detail="connection_id 또는 base_url 중 하나는 있어야 합니다.",
            )
        provider = LokiProvider(
            base_url=payload.base_url,
            auth_type=payload.auth_type.value,
            secret=payload.secret,
            label_mapping={},
        )

    try:
        result = provider.test_connection()
    except LogSourceError as exc:
        return ConnectionTestResponse(ok=False, message=str(exc))
    except ValueError as exc:
        return ConnectionTestResponse(ok=False, message=str(exc))

    return ConnectionTestResponse(
        ok=result.ok,
        message=result.message,
        latency_ms=result.latency_ms,
        details=result.details,
    )


@router.get("/{connection_id}", response_model=LokiConnectionRead)
def get_loki_connection(connection_id: int, db: Session = Depends(get_db)) -> LokiConnectionRead:
    return _to_read(_get_or_404(db, connection_id))


@router.patch("/{connection_id}", response_model=LokiConnectionRead)
def update_loki_connection(
    connection_id: int, payload: LokiConnectionUpdate, db: Session = Depends(get_db)
) -> LokiConnectionRead:
    connection = _get_or_404(db, connection_id)
    changes = payload.model_dump(exclude_unset=True)

    if "name" in changes and changes["name"] is not None:
        if _name_taken(db, changes["name"], exclude_id=connection_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"이미 같은 이름의 연결이 있습니다: {changes['name']!r}",
            )
        connection.name = changes["name"]
    if changes.get("base_url"):
        connection.base_url = changes["base_url"]
    if changes.get("auth_type") is not None:
        connection.auth_type = AuthType(changes["auth_type"]).value
    if changes.get("label_mapping") is not None:
        connection.label_mapping = dict(changes["label_mapping"])
    if changes.get("active") is not None:
        connection.active = bool(changes["active"])
    if "secret" in changes:
        # 명시적 null 은 "secret 제거", 문자열은 재암호화 저장이다.
        connection.encrypted_secret = _encrypt_secret(changes["secret"])

    db.add(connection)
    db.commit()
    db.refresh(connection)
    return _to_read(connection)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_loki_connection(connection_id: int, db: Session = Depends(get_db)) -> None:
    """정책이 참조 중일 수 있으므로 실제 삭제가 아니라 `active=false` 비활성화다."""
    connection = _get_or_404(db, connection_id)
    connection.active = False
    db.add(connection)
    db.commit()


@router.get("/{connection_id}/labels", response_model=LabelValuesResponse)
def list_labels(connection_id: int, db: Session = Depends(get_db)) -> LabelValuesResponse:
    """정책 작성 UI 용 라벨 탐색. `supports_label_discovery=False` 면 빈 결과를 준다."""
    connection = _get_or_404(db, connection_id)
    provider = _provider_for(connection)
    if not provider.supports_label_discovery:
        return LabelValuesResponse(supports_label_discovery=False)

    try:
        values = provider.list_labels()
    except LogSourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"라벨 조회에 실패했습니다: {exc}",
        ) from exc

    return LabelValuesResponse(
        labels=sorted(values),
        values=values,
        supports_label_discovery=True,
    )
