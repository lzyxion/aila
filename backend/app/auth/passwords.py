"""비밀번호 해시 — stdlib `hashlib.scrypt` 만 쓴다.

**새 의존성 금지**가 이 모듈의 제약이다 (passlib·bcrypt·argon2 모두 쓰지 않는다).
CPython 의 `hashlib.scrypt` 는 OpenSSL 의 scrypt 를 그대로 부르므로 메모리-하드
KDF 를 표준 라이브러리만으로 얻을 수 있다.

저장 포맷은 salt 를 값 안에 담는 자체 포맷이다::

    scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>

파라미터를 값 안에 적어 두는 이유는 나중에 비용을 올려도(예: n 을 2 배) **기존
행이 그대로 검증되기** 때문이다. 해시 형식이 바뀌면 전체 계정이 로그인 불능이 된다.

비교는 `hmac.compare_digest` 로만 한다 (타이밍 차이로 해시가 새지 않게).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

#: scrypt 파라미터. n*r*128 바이트를 쓰므로 maxmem 을 함께 올려야 한다.
#: n=2**14, r=8 → 약 16 MiB. 로컬 데모 서버에서 로그인 1 회가 수십 ms 수준이다.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16

#: OpenSSL 기본 maxmem(32 MiB)으로는 n=2**14, r=8 이 걸릴 수 있어 명시적으로 올린다.
_MAXMEM = 128 * SCRYPT_N * SCRYPT_R * 2

ALGORITHM = "scrypt"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _derive(password: str, salt: bytes, *, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=_MAXMEM
    )


def hash_password(password: str) -> str:
    """평문 비밀번호 → 저장용 문자열. salt 는 호출마다 새로 만든다."""
    if not password:
        raise ValueError("비밀번호가 비어 있습니다.")
    salt = os.urandom(SALT_BYTES)
    derived = _derive(password, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN)
    return "$".join(
        [ALGORITHM, str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P), _b64(salt), _b64(derived)]
    )


def verify_password(password: str, stored: str | None) -> bool:
    """저장된 해시와 평문을 비교한다. 형식이 깨졌으면 예외 대신 `False`.

    깨진 행 하나로 로그인 경로가 500 을 내면, 인증이 없는 것보다 나쁜 상태
    (아무도 못 들어가고 원인도 안 보임)가 된다.
    """
    if not password or not stored:
        return False
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != ALGORITHM:
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = _unb64(parts[4])
        expected = _unb64(parts[5])
    except (ValueError, TypeError):
        return False
    if n <= 1 or r <= 0 or p <= 0 or not salt or not expected:
        return False
    try:
        candidate = _derive(password, salt, n=n, r=r, p=p, dklen=len(expected))
    except (ValueError, MemoryError):  # pragma: no cover - 비정상 파라미터 방어
        return False
    return hmac.compare_digest(candidate, expected)


__all__ = ["ALGORITHM", "hash_password", "verify_password"]
