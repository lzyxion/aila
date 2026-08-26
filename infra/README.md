# infra/

**소유: Phase 1 — 인프라 트랙**

이 디렉터리와 저장소 루트의 `docker-compose.yml` 은 인프라 트랙이 단독 소유한다.
Phase 0 에서는 자리만 만들어 두었고, 파일은 인프라 트랙이 채운다.

## 인프라 트랙이 만들 것

설계 문서 "데모 및 테스트 환경" 절 기준.

- 루트 `docker-compose.yml` — `postgres`, `loki`, `alloy`, `grafana`, `demo-api`,
  선택적으로 `backend` / `frontend`
- `infra/loki/` — Loki 설정
- `infra/alloy/` — Alloy 설정 (데모 API stdout 수집)
- `infra/grafana/` — Loki 데이터소스 프로비저닝 (로그 적재 육안 검증용)
- `infra/demo-api/` — 의도적으로 오류를 내는 데모 API
- `infra/scenarios/` — 장애 시나리오별 `expected-analysis.md` / fixture

## 시나리오

| 시나리오 | 예상 오류 | 검증 대상 |
| --- | --- | --- |
| 결제 외부 API 타임아웃 | `TimeoutError`, 504 | 빈도 급증 감지·LLM 원인 가설 |
| DB 연결 실패 | `DatabaseConnectionError` | 오류 그룹화·대응 절차 |
| 인증 토큰 만료 | 401, `JWT expired` | 서비스·기간 필터 |
| Null 참조 | stack trace | 스택 프레임 추출 |
| 배포 직후 오류 증가 | `release` 라벨 변화 | 배포 전후 비교 |
| 비밀값 포함 | Bearer token, email | 마스킹 검증 (자동 테스트 필수) |

## 백엔드와의 접점 (계약)

- 백엔드는 `AILA_DATABASE_URL` 로 PostgreSQL 에 붙는다 (`backend/app/config.py`).
- 백엔드는 Loki 를 **읽기 전용**으로만 사용한다. 수집 경로는 건드리지 않는다.
- 백엔드 컨테이너에는 `AILA_ENCRYPTION_KEY` (Fernet 키) 를 주입해야 한다.
  키 생성: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
