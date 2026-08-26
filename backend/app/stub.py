"""Phase 0 스텁 헬퍼.

Phase 0 은 계약(타입·스키마·인터페이스·라우터 시그니처)만 고정한다.
비즈니스 로직이 없는 핸들러는 501 을 반환하며, 어느 트랙이 채워야 하는지 함께 알린다.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException, status


def not_implemented(track: str, detail: str = "") -> NoReturn:
    """501 을 던진다. `track` 은 이 핸들러를 채울 Phase 1 트랙 이름."""
    message = f"Not implemented (Phase 1 담당 트랙: {track})"
    if detail:
        message = f"{message} — {detail}"
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=message)
