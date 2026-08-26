# 02 · DB 연결 실패

| 항목 | 값 |
| --- | --- |
| 시나리오 id | `db-connection` |
| 예상 오류 | `DatabaseConnectionError`, HTTP 500 |
| 검증 대상 | 오류 그룹화 · 대응 절차 |
| 서비스 / 환경 | `order-api` / `staging` |
| 트리거 | `GET /orders`, `POST /scenarios/db-connection/burst?count=N` |

## 재현

```powershell
curl.exe -X POST "http://localhost:8081/scenarios/db-connection/burst?count=40"
```

## 로그 모양

```
order lookup failed: DatabaseConnectionError while acquiring connection
  (pool_in_use=19/20, waited=5001ms) status=500
```

예외 메시지에 **접속 대상이 그대로 들어 있다** —
`could not connect to server: Connection refused (host=postgres port=5432 database=aila)`.
호스트·포트·DB 이름은 비밀값은 아니지만, 마스킹 규칙의 "DB 연결 문자열" 항목이
어디까지를 대상으로 보는지 결정해야 하는 지점이다.
**여기에는 자격증명이 없으므로 마스킹하지 않는 것이 맞다** — 마스킹이 과하면
원인 파악에 필요한 정보까지 지워져 LLM 이 엉뚱한 가설을 낸다.
자격증명이 든 형태는 시나리오 06 에서 다룬다.

## 기대하는 그룹화 결과

- 그룹 **1 개**. `pool_in_use` 값(18~20)이 달라도 같은 fingerprint 여야 한다.
- 이 시나리오는 `service` 가 `payment-api` 가 아니라 `order-api` 다.
  컨테이너는 하나지만 JSON 본문의 `service` 가 Alloy 에서 컨테이너 라벨을 덮어쓰므로
  Loki 스트림이 갈린다. **service 라벨 기반 필터가 실제로 동작하는지** 확인하는 자리다.

## 기대하는 LLM 분석 (원인 가설)

- **요약** — 주문 조회가 DB 커넥션 획득 단계에서 실패하고 있다.
- **severity** — `critical` 근처. 읽기 경로 전체가 막힌다.
- **가설**
  1. DB 인스턴스 다운 또는 재시작 중 — 근거: `Connection refused`
  2. 커넥션 풀 고갈 — 근거: `pool_in_use=19/20`, `waited=5001ms` (풀 대기 타임아웃)
  3. 네트워크/DNS 로 `postgres` 호스트 해석 실패 — 근거: 포트까지 도달하지 못함
- **확인 절차**
  1. `docker compose ps postgres` / `pg_isready` 로 인스턴스 생존 확인
  2. `pg_stat_activity` 로 실제 연결 수와 `max_connections` 비교
  3. 애플리케이션 풀 설정(`db_pool_size`, `db_max_overflow`)과 실제 동시 요청량 비교
  4. 오류 시작 시각 기준으로 DB 재시작·마이그레이션 이력 대조
- **완화** — 풀 크기·타임아웃 조정, 재시도와 백오프, 헬스체크 기반 트래픽 차단,
  읽기 경로에 대한 degrade 응답
- **한계** — `Connection refused` 만으로는 DB 다운과 방화벽 차단을 구분할 수 없다.

## 흔한 실패 모드

- 가설 1 개만 내고 끝나는 것. 커넥션 풀 고갈과 인스턴스 다운은 대응이 완전히 다르므로
  둘 다 나와야 실용적이다.
- `pool_in_use` 숫자 차이로 그룹이 갈리는 것.
