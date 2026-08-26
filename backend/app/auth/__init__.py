"""인증 · 권한 (Phase 5).

- `passwords` — stdlib `hashlib.scrypt` 기반 해시 (새 의존성 없음)
- `service`  — 계정 CRUD · 로그인 · 세션 발급/해석/폐기 · 관리자 시드
- `dependencies` — `/api/**` 전역 보호 의존성 (viewer 는 GET 만)
- `router`   — `/api/auth/login|logout|me|users`
"""
