"""`/api/llm-connections` 라우터.

Phase 1 담당 트랙: **LLM 분석**

계약상 제약 세 가지를 여기서 지킨다.

- api_key 는 `app.crypto.encrypt()` 결과만 저장하고, **어떤 응답에도 평문을 싣지 않는다.**
  표시용 마스킹 값(`api_key_masked`)만 내보낸다.
- `is_default=True` 인 연결은 **항상 최대 하나**다 (새 기본 연결을 지정하면 기존 것을 해제).
- `DELETE` 는 실제 삭제가 아니라 `active=false` 비활성화다 — 분석 이력이 참조 중이다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.crypto import DecryptionError, EncryptionKeyMissingError, decrypt, encrypt, mask_secret
from app.db import get_db
from app.enums import LLMProviderName
from app.llm_providers import build_llm_provider, build_llm_provider_from_values
from app.models import LLMConnection
from app.providers.llm import LLMError, LLMProvider
from app.schemas.api import (
    ConnectionTestResponse,
    LLMConnectionCreate,
    LLMConnectionRead,
    LLMConnectionTestRequest,
    LLMConnectionUpdate,
)

TRACK = "LLM 분석"

router = APIRouter(prefix="/llm-connections", tags=["llm-connections"])

#: 복호화가 안 될 때 쓰는 마스킹 표시값 (평문 길이도 노출하지 않는다).
MASK_PLACEHOLDER = "****"


# ------------------------------------------------------------------ 내부 헬퍼


def _masked_api_key(connection: LLMConnection) -> str | None:
    """표시용 마스킹 값. 평문은 절대 응답에 싣지 않는다."""
    if not connection.encrypted_api_key:
        return None
    try:
        return mask_secret(decrypt(connection.encrypted_api_key))
    except (DecryptionError, EncryptionKeyMissingError):
        # 키가 바뀌었어도 목록 조회는 살아 있어야 한다.
        return MASK_PLACEHOLDER


def _to_read(connection: LLMConnection) -> LLMConnectionRead:
    return LLMConnectionRead(
        id=connection.id,
        name=connection.name,
        provider=LLMProviderName(connection.provider),
        model=connection.model,
        base_url=connection.base_url,
        is_default=connection.is_default,
        active=connection.active,
        api_key_masked=_masked_api_key(connection),
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _get_or_404(db: Session, connection_id: int) -> LLMConnection:
    connection = db.get(LLMConnection, connection_id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM 연결을 찾을 수 없습니다 (id={connection_id}).",
        )
    return connection


def _encrypt_api_key(api_key: str | None) -> str | None:
    if api_key is None or api_key == "":
        return None
    try:
        return encrypt(api_key)
    except EncryptionKeyMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


def _name_taken(db: Session, name: str, *, exclude_id: int | None = None) -> bool:
    statement = select(LLMConnection.id).where(LLMConnection.name == name)
    if exclude_id is not None:
        statement = statement.where(LLMConnection.id != exclude_id)
    return db.execute(statement).first() is not None


def _clear_other_defaults(db: Session, *, keep_id: int | None) -> None:
    """기본 연결은 최대 하나다 — 나머지 행의 `is_default` 를 내린다."""
    statement = update(LLMConnection).where(LLMConnection.is_default.is_(True))
    if keep_id is not None:
        statement = statement.where(LLMConnection.id != keep_id)
    db.execute(statement.values(is_default=False))


def _provider_for(connection: LLMConnection) -> LLMProvider:
    """저장된 연결로 어댑터를 만든다. 복호화·provider 오류는 400 으로 바꾼다."""
    try:
        return build_llm_provider(connection)
    except DecryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "저장된 API 키를 복호화할 수 없습니다 "
                "(AILA_ENCRYPTION_KEY 불일치). API 키를 다시 저장하세요."
            ),
        ) from exc
    except EncryptionKeyMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _stored_api_key(connection: LLMConnection) -> str | None:
    try:
        return decrypt(connection.encrypted_api_key) if connection.encrypted_api_key else None
    except DecryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "저장된 API 키를 복호화할 수 없습니다 "
                "(AILA_ENCRYPTION_KEY 불일치). API 키를 다시 저장하세요."
            ),
        ) from exc
    except EncryptionKeyMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


# --------------------------------------------------------------------- CRUD


@router.get("", response_model=list[LLMConnectionRead])
def list_llm_connections(db: Session = Depends(get_db)) -> list[LLMConnectionRead]:
    connections = db.execute(select(LLMConnection).order_by(LLMConnection.id)).scalars().all()
    return [_to_read(connection) for connection in connections]


@router.post("", response_model=LLMConnectionRead, status_code=status.HTTP_201_CREATED)
def create_llm_connection(
    payload: LLMConnectionCreate, db: Session = Depends(get_db)
) -> LLMConnectionRead:
    """api_key 는 암호화 저장하고 응답에는 마스킹된 값만 싣는다.

    `is_default=True` 로 만들면 기존 기본 연결은 해제한다 (기본 연결은 최대 하나).
    """
    if _name_taken(db, payload.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 같은 이름의 LLM 연결이 있습니다: {payload.name!r}",
        )

    connection = LLMConnection(
        name=payload.name,
        provider=LLMProviderName(payload.provider).value,
        model=payload.model,
        base_url=payload.base_url,
        encrypted_api_key=_encrypt_api_key(payload.api_key),
        is_default=payload.is_default,
        active=payload.active,
    )
    db.add(connection)
    db.flush()
    if connection.is_default:
        _clear_other_defaults(db, keep_id=connection.id)
    db.commit()
    db.refresh(connection)
    return _to_read(connection)


@router.post("/test", response_model=ConnectionTestResponse)
def test_llm_connection(
    payload: LLMConnectionTestRequest, db: Session = Depends(get_db)
) -> ConnectionTestResponse:
    """연결 테스트도 실제 과금 호출이다 — 최소 토큰으로 보낸다.

    `connection_id` 를 주면 저장된 값으로 만들고, 함께 넘어온 provider/model/base_url/
    api_key 는 덮어쓴다 — "키를 바꿔 보고 저장" 흐름을 저장 없이 확인할 수 있어야 한다.
    """
    if payload.connection_id is not None:
        connection = _get_or_404(db, payload.connection_id)
        # 저장값 검증(복호화·provider 지원 여부)을 먼저 태운다.
        _provider_for(connection)
        provider_name = (
            LLMProviderName(payload.provider).value if payload.provider else connection.provider
        )
        model = payload.model or connection.model
        base_url = payload.base_url or connection.base_url
        api_key = payload.api_key or _stored_api_key(connection)
    else:
        if not payload.provider or not payload.model:
            raise HTTPException(
                status_code=422,
                detail="connection_id 또는 provider·model 조합 중 하나는 있어야 합니다.",
            )
        provider_name = LLMProviderName(payload.provider).value
        model = payload.model
        base_url = payload.base_url
        api_key = payload.api_key

    try:
        provider = build_llm_provider_from_values(
            provider=provider_name,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        result = provider.test_connection()
    except LLMError as exc:
        return ConnectionTestResponse(ok=False, message=str(exc))
    except ValueError as exc:
        return ConnectionTestResponse(ok=False, message=str(exc))

    return ConnectionTestResponse(
        ok=result.ok,
        message=result.message,
        latency_ms=result.latency_ms,
        details=result.details,
    )


@router.get("/{connection_id}", response_model=LLMConnectionRead)
def get_llm_connection(connection_id: int, db: Session = Depends(get_db)) -> LLMConnectionRead:
    return _to_read(_get_or_404(db, connection_id))


@router.patch("/{connection_id}", response_model=LLMConnectionRead)
def update_llm_connection(
    connection_id: int, payload: LLMConnectionUpdate, db: Session = Depends(get_db)
) -> LLMConnectionRead:
    connection = _get_or_404(db, connection_id)
    changes = payload.model_dump(exclude_unset=True)

    if "name" in changes and changes["name"] is not None:
        if _name_taken(db, changes["name"], exclude_id=connection_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"이미 같은 이름의 LLM 연결이 있습니다: {changes['name']!r}",
            )
        connection.name = changes["name"]
    if changes.get("provider") is not None:
        connection.provider = LLMProviderName(changes["provider"]).value
    if changes.get("model"):
        connection.model = changes["model"]
    if "base_url" in changes:
        # 명시적 null 은 "기본 엔드포인트로 되돌린다" 는 뜻이다.
        connection.base_url = changes["base_url"] or None
    if changes.get("active") is not None:
        connection.active = bool(changes["active"])
    if "api_key" in changes:
        # 명시적 null 은 "키 제거", 문자열은 재암호화 저장이다.
        connection.encrypted_api_key = _encrypt_api_key(changes["api_key"])
    if changes.get("is_default") is not None:
        connection.is_default = bool(changes["is_default"])

    # 비활성 연결이 기본으로 남으면 분석이 죽은 연결을 고른다.
    if not connection.active:
        connection.is_default = False

    db.add(connection)
    db.flush()
    if connection.is_default:
        _clear_other_defaults(db, keep_id=connection.id)
    db.commit()
    db.refresh(connection)
    return _to_read(connection)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_llm_connection(connection_id: int, db: Session = Depends(get_db)) -> None:
    """분석 이력이 참조하므로 실제 삭제가 아니라 `active=false` 비활성화다."""
    connection = _get_or_404(db, connection_id)
    connection.active = False
    # 비활성 연결은 기본 연결로 남지 않는다.
    connection.is_default = False
    db.add(connection)
    db.commit()
