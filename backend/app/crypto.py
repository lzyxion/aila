"""연결 secret·LLM API 키 대칭 암호화.

`AILA_ENCRYPTION_KEY`(Fernet 키)로 암복호화한다. 평문 저장과 KMS 사이의
MVP 수준 중간점이며, 키 자체는 환경변수로만 주입한다 — DB 나 코드에 두지 않는다.

계약: `log_source_connections.encrypted_secret`, `llm_connections.encrypted_api_key`
컬럼에는 반드시 `encrypt()` 결과(문자열)만 넣는다. 평문을 직접 넣지 말 것.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class EncryptionKeyMissingError(RuntimeError):
    """AILA_ENCRYPTION_KEY 가 설정되지 않았다."""


class DecryptionError(RuntimeError):
    """토큰이 손상되었거나 다른 키로 암호화되었다."""


def generate_key() -> str:
    """새 Fernet 키를 만든다. (운영 배포 전 1 회, 환경변수로 주입)"""
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise EncryptionKeyMissingError(
            "AILA_ENCRYPTION_KEY 가 설정되지 않았습니다. "
            "`python -c \"from app.crypto import generate_key; print(generate_key())\"` "
            "로 생성해 주입하세요."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:  # 잘못된 형식의 키
        raise EncryptionKeyMissingError(f"AILA_ENCRYPTION_KEY 형식이 올바르지 않습니다: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """평문을 암호화해 DB 저장용 문자열로 만든다."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """`encrypt()` 결과를 평문으로 되돌린다."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError("암호문을 복호화할 수 없습니다 (키 불일치 또는 손상).") from exc


def mask_secret(plaintext: str, *, keep: int = 4) -> str:
    """화면 표시용 마스킹. 저장된 키는 절대 평문으로 API 응답에 싣지 않는다."""
    if not plaintext:
        return ""
    if len(plaintext) <= keep:
        return "*" * len(plaintext)
    return "*" * (len(plaintext) - keep) + plaintext[-keep:]
