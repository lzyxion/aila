# 시나리오 기준선 비교 리포트 — 2026-08-26

실 LLM 분석 결과와 `infra/scenarios/*/expected-analysis.md` 기준선을 나란히 둔다.
**비교 판정은 자동이 아니다** — 각 시나리오의 체크리스트에 사람이 기입한다 (설계 문서의 검증 절차).

- LLM: `openai/gpt-4o-mini` (연결 #2 aila)
- query-run: #8 (정책 #3, fetched 940, 그룹 11)
- 총 토큰: 입력 12,649 / 출력 2,113 · 총 비용: $0.003166 (추정)
- 결과의 심각도·confidence 는 **LLM 추정값**이며 발생량 지표와 무관하다.

## 01 · PaymentGatewayTimeout

그룹 #36 · 발생 304건 · 작업 #8 · 토큰 2146/352 · 비용 $0.000533 (추정)

### LLM 분석 결과 (openai/gpt-4o-mini)

- **요약**: 결제 승인 실패가 발생하는 문제
- **심각도 (LLM 추정)**: high
- **가설**:
  1. 결제 게이트웨이가 응답하지 않음으로 인해 타임아웃 발생 (confidence 0.9, 근거: 'upstream https://gateway.example.invalid/v2/charges did not respond within 30000ms'라는 메시지가 여러 로그에서 나타남., 모든 로그에서 'PaymentGatewayTimeout' 예외가 발생함.)
  2. 네트워크 지연 또는 게이트웨이 서버의 처리 지연으로 인한 응답 지연 (confidence 0.8, 근거: 특정한 응답 시간이 44963ms, 44893ms, 44938ms로 초과하여 개별 요청이 타임아웃 발생., HTTP 상태 코드가 504인 점에서 게이트웨이에서 응답이 지연됨을 나타냄.)
- **확인 절차**: 결제 게이트웨이의 상태와 성능 모니터링 확인 → 해당 시간대에 발생한 네트워크 문제나 장애 기록 검토 → 타임아웃이 발생한 특정 요청에 대한 응답 시간을 분석
- **완화 조치**: 결제 게이트웨이의 성능 최적화 검토 및 개선 / 타임아웃 설정 시간의 조정 / 비상시 다른 결제 게이트웨이로 우회할 수 있는 옵션 구현
- **한계 (limitations)**:
  - 로그만으로는 게이트웨이의 구체적인 응답 지연 원인을 확인할 수 없음.
  - 실제 네트워크 상태와 게이트웨이의 성능 데이터를 알 수 없어 결정을 내리기 어려움.

### 기대 기준선 (`infra/scenarios/01-payment-timeout/expected-analysis.md`)

<details><summary>기준선 전문 펼치기</summary>

# 01 · 결제 외부 API 타임아웃

| 항목 | 값 |
| --- | --- |
| 시나리오 id | `payment-timeout` |
| 예상 오류 | `PaymentGatewayTimeout` (= `TimeoutError` 하위), HTTP 504 |
| 검증 대상 | 빈도 급증 감지 · LLM 원인 가설 |
| 서비스 / 환경 | `payment-api` / `staging` |
| 트리거 | `GET /payment/charge`, `POST /scenarios/payment-timeout/burst?count=N` |

> 이 문서는 **기준선**이다. LLM 결과를 자동 채점하지는 않지만, 프롬프트나 정규화
> 규칙을 고친 뒤 결과가 나아졌는지 사람이 비교할 대상이 없으면 개선을 판단할 수 없다.

## 재현

```powershell
# 평상시 유입은 백그라운드 루프가 이미 만들고 있다. 급증만 따로 만든다.
curl.exe -X POST "http://localhost:8081/scenarios/payment-timeout/burst?count=120"
```

## 로그 모양

`sample-logs.jsonl` 참조. 핵심은 **메시지에 가변값이 섞여 있다**는 점이다.

```
payment authorization failed: gateway timeout after 31117ms
  (order_id=ORD-564994, amount=KRW 386000) status=504
```

`31117ms`, `ORD-564994`, `386000` 이 매번 달라진다. 정규화가 이 세 값을
플레이스홀더로 치환하지 못하면 **같은 오류가 발생 건수만큼 다른 그룹으로 쪼개진다.**
이 시나리오가 그룹화 규칙의 1 차 리트머스인 이유다.

## 기대하는 그룹화 결과

- 그룹 **1 개**. `service=payment-api` + `exception=PaymentGatewayTimeout` +
  정규화 메시지 + 상위 스택 프레임(`scenarios.py:payment_timeout`) 으로 fingerprint 가 잡힌다.
- burst 120 회 + 백그라운드 유입이 한 그룹의 `count` 로 합산되어야 한다.
- `gateway` 필드는 `pg-alpha` / `pg-beta` 두 값을 오간다. **이 차이로 그룹이 갈리면 안 된다**
  (필드는 fingerprint 입력이 아니다).

## 기대하는 LLM 분석 (원인 가설)

- **요약** — 결제 게이트웨이 요청이 30 초 타임아웃에 걸려 승인 처리가 실패하고 있다.
- **severity** — `high` 근처. 결제 실패는 매출에 직결된다.
- **가설** (근거와 함께 복수로 나와야 한다)
  1. 외부 결제 게이트웨이(`gateway.example.invalid`) 지연 또는 장애 — 근거: `504`, `TimeoutError`, `elapsed_ms > 30000`
  2. 클라이언트 타임아웃 설정(30s)이 게이트웨이 p99 보다 짧음 — 근거: 경과 시간이 임계값 바로 위에 몰려 있음
  3. 네트워크 경로/DNS 문제로 커넥션 수립 자체가 지연 — 근거: 특정 gateway 값에 치우치지 않음
- **확인 절차**
  1. 결제 게이트웨이 상태 페이지와 우리 쪽 아웃바운드 지표(연결 수립 시간) 비교
  2. `count_over_time` 으로 급증 시작 시각을 특정하고 그 시각의 배포·설정 변경 이력 대조
  3. `gateway` 필드별 분포 확인 — 한쪽에만 몰리면 게이트웨이 장애, 고르면 우리 쪽 네트워크
- **완화** — 타임아웃·재시도(지수 백오프) 정책 점검, 회로 차단기 도입, 대체 게이트웨이 라우팅
- **한계** (반드시 포함되어야 한다) — 로그만으로 외부 서비스 장애를 확정할 수 없다.
  게이트웨이 응답 코드가 아니라 우리 쪽 타임아웃 기록만 있다.

## 흔한 실패 모드 (여기서 걸러야 한다)

- LLM 이 "외부 결제 API 장애입니다" 라고 **단정**하는 것 — `hypotheses` / `limitations`
  스키마가 이걸 구조적으로 막아야 한다.
- 그룹이 여러 개로 쪼개지는 것 — 정규화가 `order_id` / 금액 / 경과시간을 못 지운 것이다.
- 발생 건수를 로그 라인 수로 세는 것 — 급증 시에는 정책 상한과 Loki 5,000 줄 한도에 걸려
  **실제보다 적게** 나온다. 건수는 `count_over_time` 으로 구해야 한다.

</details>

### 판정 (사람 기입)

- [ ] 가설 중 하나가 기대 원인을 포함한다
- [ ] 확인 절차가 기대 절차의 핵심 단계를 담고 있다
- [ ] limitations 가 "로그만으로 확정 불가" 취지를 스스로 밝힌다
- [ ] 기대 문서의 "흔한 실패 모드"에 해당하지 않는다
- 메모:


---

## 02 · DatabaseConnectionError

그룹 #38 · 발생 161건 · 작업 #9 · 토큰 2139/285 · 비용 $0.000492 (추정)

### LLM 분석 결과 (openai/gpt-4o-mini)

- **요약**: order-api의 DatabaseConnectionError 발생 원인 가설
- **심각도 (LLM 추정)**: high
- **가설**:
  1. 데이터베이스 연결 수 제한 초과 (confidence 0.9, 근거: 정규화 메시지에 'pool_in_use=20/20'로 표시되어 있어 모든 연결 풀이 사용 중임을 나타냄., 로그에서 'DatabaseConnectionError while acquiring connection'이 반복적으로 발생함.)
  2. 데이터베이스 서버 접근 불가 (confidence 0.8, 근거: 모든 로그에서 'could not connect to server: Connection refused' 메시지가 나타남., 데이터베이스 호스트가 'postgres'로 설정되었지만 연결이 거부되고 있음.)
- **확인 절차**: 데이터베이스 서버(postgres) 상태 확인 → max_connections 설정 확인 및 조정 진행 → order-api의 데이터베이스 연결 요청 수 확인
- **완화 조치**: 데이터베이스 연결 수를 늘려 max_connections를 조정 / order-api의 연결 관리 전략 개선 (예: 연결 풀링)
- **한계 (limitations)**:
  - 로그에서 부하량이나 트래픽 패턴에 대한 정보가 부족하여 확실한 원인 분석이 어려움.
  - 데이터베이스 서버의 현재 상태나 설정이 로그에 없으므로 실시간 모니터링이 필요함.

### 기대 기준선 (`infra/scenarios/02-db-connection/expected-analysis.md`)

<details><summary>기준선 전문 펼치기</summary>

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

</details>

### 판정 (사람 기입)

- [ ] 가설 중 하나가 기대 원인을 포함한다
- [ ] 확인 절차가 기대 절차의 핵심 단계를 담고 있다
- [ ] limitations 가 "로그만으로 확정 불가" 취지를 스스로 밝힌다
- [ ] 기대 문서의 "흔한 실패 모드"에 해당하지 않는다
- 메모:


---

## 03 · TokenExpiredError

그룹 #37 · 발생 262건 · 작업 #10 · 토큰 2031/255 · 비용 $0.000458 (추정)

### LLM 분석 결과 (openai/gpt-4o-mini)

- **요약**: JWT 인증 토큰 만료로 인한 인증 실패 사건이 발생하고 있다.
- **심각도 (LLM 추정)**: high
- **가설**:
  1. 유효기간이 지난 JWT를 사용하고 있어서 인증이 거부되고 있다. (confidence 0.9, 근거: 로그 메시지에서 'JWT expired'가 명확하게 언급되어 있음., 발생한 예외 타입이 'TokenExpiredError'로, 이는 JWT 서명이 만료되었음을 알리는 정보를 제공함.)
- **확인 절차**: JWT의 만료 시간 설정 확인 → 만료된 JWT를 사용하는 클라이언트의 요청 패턴 분석 → 비정상적인 요청이 발생할 수 있는 클라이언트 식별
- **완화 조치**: JWT의 만료 시간을 연장하는 방안 논의 / 만료된 토큰의 경우 리프레시 토큰을 사용하여 재인증 할 수 있는 정책 설정
- **한계 (limitations)**:
  - JWT 만료의 구체적인 이유 (예: 클라이언트의 요청 주기, 사용자 행동 등)는 로그를 통해 확인할 수 없음.
  - 여전히 클라이언트 측에서 제대로 토큰을 새로 발급 받지 못하는 상황일 수 있음.

### 기대 기준선 (`infra/scenarios/03-auth-expired/expected-analysis.md`)

<details><summary>기준선 전문 펼치기</summary>

# 03 · 인증 토큰 만료

| 항목 | 값 |
| --- | --- |
| 시나리오 id | `auth-expired` |
| 예상 오류 | `TokenExpiredError` / `JWT expired`, HTTP 401 |
| 검증 대상 | 서비스 · 기간 필터 |
| 서비스 / 환경 | `auth-api` / `staging` |
| 트리거 | `GET /auth/me`, `POST /scenarios/auth-expired/burst?count=N` |

## 재현

```powershell
curl.exe -X POST "http://localhost:8081/scenarios/auth-expired/burst?count=50"
```

## 이 시나리오의 함정 — level 이 `ERROR` 가 아니다

이 로그는 `level=WARNING` 으로 나간다. 토큰 만료는 **정상적인 클라이언트 오류**이고,
실제 서비스도 보통 이렇게 찍는다. 그래서 설계 문서의 예시 LogQL

```logql
{service="auth-api", environment="staging"} | json | level="ERROR"
```

로는 **한 건도 잡히지 않는다.** 이것이 의도된 함정이다.

정책 저장 화면의 "저장 전 실행 결과 미리보기" 가 없으면, 이 정책은 조용히 빈 결과를
내는 채로 굳는다. 미리보기가 왜 MVP 에 있어야 하는지를 보여주는 시나리오다.

잡으려면 이렇게 쓴다.

```logql
{service="auth-api"} | json | level=~"ERROR|WARNING"
```

## 기대하는 그룹화 결과

- 그룹 **1 개**. `sub=user-2159`, `JWT expired 3327s ago` 의 숫자가 지워져야 한다.
- `service` 필터를 `auth-api` 로 좁히면 다른 시나리오 로그가 하나도 섞이지 않아야 한다.
- 기간 필터 검증: burst 를 한 번 낸 뒤 그 앞뒤 5 분으로 조회 구간을 좁혔다 넓혔다 하며
  건수가 `count_over_time` 결과와 맞는지 본다.

## 기대하는 LLM 분석 (원인 가설)

- **요약** — 만료된 액세스 토큰으로 들어온 요청이 거부되고 있다.
- **severity** — `low` ~ `medium`. 단, **건수가 급증했다면 얘기가 다르다** —
  LLM 이 발생량을 근거로 심각도를 조정하려 들면 그건 과한 추론이다.
  `severity` 는 대표 로그 몇 개로 추정한 값이므로 발생량 기반 지표와 분리해 표시한다.
- **가설**
  1. 정상 동작 — 클라이언트가 만료 토큰으로 재시도 중이고 리프레시가 뒤따를 것
  2. 토큰 리프레시 로직 결함으로 갱신 없이 계속 재시도 — 근거: 같은 오류가 지속 반복
  3. 서버 시계 오차(NTP) 로 유효한 토큰이 만료로 판정 — 근거: 만료 경과 시간이 작은 값에 몰림
- **확인 절차**
  1. 만료 경과 시간(`JWT expired Ns ago`) 분포 확인 — 몇 초대에 몰리면 시계 오차 의심
  2. 같은 `sub` 가 반복 실패하는지 확인 (리프레시 실패)
  3. 토큰 TTL 설정과 클라이언트 갱신 주기 비교
- **완화** — 클라이언트 리프레시 흐름 점검, 401 응답에 갱신 힌트 포함, 서버 NTP 동기 확인
- **한계** — 이 로그만으로는 정상 동작과 결함을 구분할 수 없다. 사용자별 재시도 패턴이 필요하다.

## 흔한 실패 모드

- `level="ERROR"` 정책으로 조회해 놓고 "오류가 없다"고 결론 내는 것.
- 정상 동작 가능성을 아예 언급하지 않는 것 — 401 이 항상 장애는 아니다.

</details>

### 판정 (사람 기입)

- [ ] 가설 중 하나가 기대 원인을 포함한다
- [ ] 확인 절차가 기대 절차의 핵심 단계를 담고 있다
- [ ] limitations 가 "로그만으로 확정 불가" 취지를 스스로 밝힌다
- [ ] 기대 문서의 "흔한 실패 모드"에 해당하지 않는다
- 메모:


---

## 04 · AttributeError

그룹 #39 · 발생 143건 · 작업 #11 · 토큰 2344/475 · 비용 $0.000637 (추정)

### LLM 분석 결과 (openai/gpt-4o-mini)

- **요약**: 이 오류는 유저 프로파일이 로드되지 않아서 발생하는 AttributeError입니다.
- **심각도 (LLM 추정)**: high
- **가설**:
  1. 유저 프로파일이 데이터베이스에 존재하지 않거나 로드할 수 없는 상태입니다. (confidence 0.8, 근거: 모든 로그에서 'NoneType' 객체가 반환되어 display_name 속성을 접근할 수 없다는 에러가 발생합니다., profile 변수는 유저 ID와 관련이 있지만, 프로파일이 없을 경우 None으로 반환되는 것으로 보입니다.)
  2. user_id를 기반으로 프로파일을 로드하는 로직에 결함이 있을 수 있습니다. (confidence 0.6, 근거: 로그에 있는 '_load_profile(user_id)' 함수가 유저 프로파일 로딩 중 에러를 발생시킴을 보여줍니다., 특정 유저 ID에 대해 반복적으로 동일한 에러가 발생하고 있습니다.)
- **확인 절차**: 이벤트가 발생한 user_id들을 데이터베이스에서 조회하여 프로파일이 존재하는지 확인합니다. → 유저 프로파일 로드를 담당하는 메소드를 리뷰하고, 해당 메소드에서 어떤 이유로 None이 반환될 수 있는지 분석합니다. → 로그에서 발생하는 유사한 패턴의 오류를 확인하여 공통적인 문제가 있는지 조사합니다.
- **완화 조치**: 유저 프로파일이 없을 경우에 대한 예외 처리 로직을 추가하여 NoneType 에러를 방지합니다. / 유저 프로파일 로딩이 실패하는 경우 에러 메시지를 사용자에게 보다 명확히 전달합니다.
- **한계 (limitations)**:
  - 로그에는 프로파일이 왜 로드되지 않았는지에 대한 구체적인 정보가 포함되어 있지 않습니다.
  - 유저 ID와 관련된 추가 정보(예: 유저가 삭제되었는지, 비활성화 상태인지 등)에 대한 세부사항은 로그에서 확인할 수 없습니다.

### 기대 기준선 (`infra/scenarios/04-null-reference/expected-analysis.md`)

<details><summary>기준선 전문 펼치기</summary>

# 04 · Null 참조

| 항목 | 값 |
| --- | --- |
| 시나리오 id | `null-reference` |
| 예상 오류 | `AttributeError: 'NoneType' object has no attribute 'display_name'`, HTTP 500 |
| 검증 대상 | **스택 프레임 추출** |
| 서비스 / 환경 | `payment-api` / `staging` |
| 트리거 | `GET /profile`, `POST /scenarios/null-reference/burst?count=N` |

## 재현

```powershell
curl.exe -X POST "http://localhost:8081/scenarios/null-reference/burst?count=25"
```

## 로그 모양

`stacktrace` 필드에 **개행이 든 문자열 하나**로 담긴다. 여러 줄로 출력하면 Alloy 가
줄마다 별개 엔트리로 읽어 그룹화가 무의미해지므로, demo-api 는 의도적으로 한 줄로 낸다.

```
Traceback (most recent call last):
  File "/app/app/scenarios.py", line 263, in null_reference
    _load_profile(user_id)
  File "/app/app/scenarios.py", line 241, in _load_profile
    _render_display_name(profile)
  File "/app/app/scenarios.py", line 250, in _render_display_name
    return profile.display_name.strip()
           ^^^^^^^^^^^^^^^^^^^^
AttributeError: 'NoneType' object has no attribute 'display_name'
```

호출 경로가 3 단계인 **진짜 트레이스백**이다. 스택 프레임 추출 로직을 프레임이 하나뿐인
가짜 스택으로 테스트하면 "상위 프레임만 쓴다"는 설계 결정을 검증할 수 없다.

## 기대하는 그룹화 결과

- 그룹 **1 개**. `user_id=2240` 의 숫자가 지워져야 한다.
- fingerprint 입력은 `service` + `AttributeError` + 정규화 메시지 +
  **상위 스택 프레임** 이다. 여기서 상위 프레임은 예외가 실제로 발생한
  `scenarios.py:_render_display_name` 쪽이다.
- **스택 전체를 해시하면 안 된다.** 호출 경로가 조금만 달라져도 같은 버그가 다른 그룹으로
  쪼개진다. 반대로 메시지만 해시하면 서로 다른 원인이 한 그룹으로 뭉친다.
  이 시나리오는 그 중간을 잡았는지 확인하는 자리다.
- 스택의 파일 경로에 든 **줄 번호는 정규화 대상**이다. 코드가 한 줄만 밀려도
  같은 버그가 새 그룹이 되어서는 안 된다.

## 기대하는 LLM 분석 (원인 가설)

- **요약** — 프로필 조회가 결과 없음(None)을 반환했는데 호출부가 그대로 속성에 접근해 실패한다.
- **severity** — `medium`. 특정 사용자 경로만 깨진다.
- **가설**
  1. `_fetch_profile_row` 가 해당 user_id 에 대해 행을 찾지 못해 `None` 반환 — 근거: 스택 최상단이 속성 접근
  2. 데이터 정합성 문제 — 참조는 있으나 프로필 레코드가 삭제됨
  3. 상류에서 잘못된 user_id 가 전달됨
- **확인 절차**
  1. 실패한 `user_id` 몇 개로 프로필 레코드 존재 여부 직접 조회
  2. `_fetch_profile_row` 의 None 반환 경로에 방어 코드/로그가 있는지 확인
  3. 최근 배포에서 프로필 조회 쿼리나 스키마가 바뀌었는지 확인
- **완화** — 호출부에서 None 검사 후 404 로 응답, 저장소 계층이 None 대신 명시적 예외를 던지도록 변경
- **한계** — 로그에 user_id 는 있으나 그 사용자의 데이터 상태는 알 수 없다.

## 흔한 실패 모드

- 상위 프레임을 못 뽑아 `exception` 만으로 그룹화 → 서로 다른 위치의 `AttributeError` 가 한 그룹.
- 스택 전체(줄 번호 포함) 해시 → burst 25 건이 여러 그룹으로 분리.
- 스택트레이스가 여러 로그 라인으로 쪼개져 들어오는 것 (demo-api 는 그렇지 않지만,
  실제 시스템에서는 흔하다 — 어댑터가 이 상황을 어떻게 다룰지는 별도 과제다).

</details>

### 판정 (사람 기입)

- [ ] 가설 중 하나가 기대 원인을 포함한다
- [ ] 확인 절차가 기대 절차의 핵심 단계를 담고 있다
- [ ] limitations 가 "로그만으로 확정 불가" 취지를 스스로 밝힌다
- [ ] 기대 문서의 "흔한 실패 모드"에 해당하지 않는다
- 메모:


---

## 05 · ConfigurationError

그룹 #46 · 발생 1건 · 작업 #12 · 토큰 1460/295 · 비용 $0.000396 (추정)

### LLM 분석 결과 (openai/gpt-4o-mini)

- **요약**: ConfigurationError를 유발한 원인 분석
- **심각도 (LLM 추정)**: high
- **가설**:
  1. 지정된 환경에 필요한 설정이 누락됨. (confidence 0.85, 근거: 에러 메시지에 따르면 'PAYMENT_GATEWAY_V2_URL' 설정이 환경에 없다고 나옴.)
  2. 배포 프로세스 중 설정 항목이 올바르게 로드되지 않음. (confidence 0.7, 근거: 배포 후 요청이 실패했다고 하며, 그 과정에서 필요한 설정이 누락되었음을 나타냄.)
- **확인 절차**: PAYMENT_GATEWAY_V2_URL 환경 변수가 staging 환경에 설정되어 있는지 확인한다. → 배포 스크립트에서 환경 변수가 올바르게 주입되는지 검토한다. → 변경한 설정이 정확히 반영되었는지 확인하기 위해 다시 배포를 시도한다.
- **완화 조치**: staging 환경에 필요한 설정(PAYMENT_GATEWAY_V2_URL)을 추가한다. / 배포 전 모든 필수 설정 항목을 검토하여 추가함으로써 향후 문제를 예방한다.
- **한계 (limitations)**:
  - 로그에서는 정확히 어떤 이유로 설정이 누락되었는지에 대한 구체적인 정보가 부족하다.
  - 다른 환경 변수들의 상태나 설정 등 추가적인 컨텍스트가 결여되어 있다.

### 기대 기준선 (`infra/scenarios/05-post-deploy-spike/expected-analysis.md`)

<details><summary>기준선 전문 펼치기</summary>

# 05 · 배포 직후 오류 증가

| 항목 | 값 |
| --- | --- |
| 시나리오 id | `post-deploy-spike` |
| 예상 오류 | `ConfigurationError`, HTTP 500 — 그리고 **`release` 라벨 변화** |
| 검증 대상 | 배포 전후 비교 기반 확장 |
| 서비스 / 환경 | `payment-api` / `staging` |
| 트리거 | `POST /admin/deploy?release=v1.5.0&spike_seconds=180` |

## 재현

```powershell
# 1) 배포 전 상태를 몇 분 쌓는다 (백그라운드 루프가 알아서 만든다). release=v1.4.2
# 2) 배포
curl.exe -X POST "http://localhost:8081/admin/deploy?release=v1.5.0&spike_seconds=180"
# 3) 이후 3 분 동안 ConfigurationError 가 몰리고 전체 오류율이 오른다
# 4) 되돌리기 (release 를 토글한다)
curl.exe -X POST "http://localhost:8081/admin/deploy"
```

배포 시각은 `message="deployment completed: v1.4.2 -> v1.5.0"` 인 INFO 로그로 남는다.
분석 시 "배포 이후"의 기준 시각은 이 로그에서 얻는다.

## 확인 쿼리

```logql
# release 별 오류 추이 — 계단이 보여야 한다
sum by (release) (count_over_time({service="payment-api", level="ERROR"}[1m]))

# 새 release 에서만 나는 오류
{service="payment-api", release="v1.5.0"} | json | exception="ConfigurationError"
```

Grafana 대시보드 "AILA — Loki 유입 검증" 의 *release 별 ERROR 추이* 패널이 같은 쿼리다.

## 기대하는 그룹화 결과

- `ConfigurationError` 그룹이 **새로 생긴다** (배포 전에는 0 건).
- 기존 `PaymentGatewayTimeout` 그룹은 건수만 늘고 fingerprint 는 그대로여야 한다.
  release 는 fingerprint 입력이 **아니다** — 같은 버그가 배포마다 새 그룹이 되면
  "이 오류는 지난 배포에도 있었나" 를 답할 수 없다.
- 대신 `release` 는 그룹의 라벨 집합에 남아, 그룹 상세에서 배포별 분포를 볼 수 있어야 한다.

> **MVP 의 구조적 한계** — `error_groups` 가 `query_run_id` 에 매달려 있으므로
> 그룹은 조회 1 회 안에서만 유효하다. "배포 전후 비교"를 그룹 엔티티 수준에서
> 자동으로 해 주지는 못한다. 이 시나리오가 검증하는 것은 **fingerprint 가 결정적이라
> 조회 회차가 달라도 같은 값을 갖는다**는 것과, 같은 fingerprint 의 기존 분석 결과를
> 조인으로 함께 보여준다는 것까지다. 그 이상은 [향후 확장] 이다.

## 기대하는 LLM 분석 (원인 가설)

- **요약** — v1.5.0 배포 이후 필수 설정값 누락으로 결제 요청이 실패하고 있다.
- **severity** — `critical`. 새 배포의 전면 롤백 판단이 필요한 상황이다.
- **가설**
  1. v1.5.0 이 새로 요구하는 `PAYMENT_GATEWAY_V2_URL` 이 staging 환경에 주입되지 않음 —
     근거: 오류가 `release=v1.5.0` 에서만 나타남, 배포 시각과 시작 시각이 일치
  2. 설정 주입 경로(ConfigMap/환경변수) 자체가 반영되지 않음
  3. 코드가 설정 부재 시 기본값 없이 즉시 실패하도록 작성됨 (설계 문제)
- **확인 절차**
  1. 배포 로그(`deployment completed`) 시각과 오류 시작 시각 대조 — 두 값이 붙어 있으면 배포 인과
  2. `release=v1.4.2` 구간과 `release=v1.5.0` 구간의 `count_over_time` 비교
  3. 대상 환경의 실제 환경변수 확인
  4. v1.5.0 변경 목록에서 새 설정 항목 확인
- **완화** — 즉시 설정 주입 후 재기동, 안 되면 v1.4.2 롤백.
  중기적으로는 기동 시 필수 설정 검증(fail fast at startup)으로 옮겨 요청 단위 실패를 없앤다.
- **한계** — 로그는 설정이 없다고만 말한다. 배포 파이프라인의 어느 단계에서 누락됐는지는 알 수 없다.

## 흔한 실패 모드

- 배포 시각을 근거로 쓰지 않는 것. `release` 라벨과 `deployment completed` 로그가
  프롬프트에 들어가지 않으면 LLM 은 "설정 누락"까지만 말하고 배포와 연결하지 못한다.
  → 프롬프트에 **선택적으로 배포 버전과 최근 오류 추이**를 넣기로 한 이유가 이것이다.
- release 를 fingerprint 에 넣어 배포마다 그룹이 새로 생기는 것.

</details>

### 판정 (사람 기입)

- [ ] 가설 중 하나가 기대 원인을 포함한다
- [ ] 확인 절차가 기대 절차의 핵심 단계를 담고 있다
- [ ] limitations 가 "로그만으로 확정 불가" 취지를 스스로 밝힌다
- [ ] 기대 문서의 "흔한 실패 모드"에 해당하지 않는다
- 메모:


---

## 06 · PaymentRetryError

그룹 #40 · 발생 44건 · 작업 #13 · 토큰 2529/451 · 비용 $0.000650 (추정)

### LLM 분석 결과 (openai/gpt-4o-mini)

- **요약**: payment-api에서 발생한 PaymentRetryError으로 인해 결제 재시도가 실패하는 문제에 대한 가설 분석입니다.
- **심각도 (LLM 추정)**: high
- **가설**:
  1. 결제 시스템의 upstream 서비스가 정상적으로 응답하지 않음으로 인해 결제 요청이 실패하고, 이로 인해 재시도가 발생함. (confidence 0.8, 근거: 로그에서 'upstream refused for <MASKED:EMAIL>' 메시지가 여러 번 나타남., 모든 에러 로그에서 http_status가 502라는 결과가 동일하여, 이는 서버 측에서 정상적으로 처리되지 않음을 나타냄.)
  2. Authorization 토큰의 만료 또는 부정확함으로 인해 결제 요청이 거부됨. (confidence 0.5, 근거: 로그의 authorization 부분에 '<MASKED:BEARER_TOKEN>'이 포함되어 있으며, 유효하지 않은 토큰일 경우 결제 요청이 실패할 수 있음.)
  3. 사용자가 입력한 결제 정보가 올바르지 않아 재시도가 반복됨. (confidence 0.4, 근거: 로그에서 반환되는 카드 정보가 '<MASKED:CARD>'으로 마스킹되어 있어, 카드 정보가 잘못되었을 수 있음.)
- **확인 절차**: upstream 결제 서버의 상태를 확인하여 서비스가 정상인지 점검한다. → Authorization 토큰의 유효성을 확인하고, 필요 시 새로 발급받는다. → 레거시 코드에서 오류의 원인이 되는 특정 요청 정보를 추가 로그로 남기도록 수정한다.
- **완화 조치**: 결제 요청 시 502 에러로 인한 재시도를 설정할 수 있는 임계 값을 조정한다. / upstream 서비스 응답을 모니터링하여 시스템 장애 발생 시 경고를 발송하도록 설정한다.
- **한계 (limitations)**:
  - 로그에서 요청에 대한 더 자세한 정보(예: 요청 URL, 요청 본문 등)가 없으므로 정확한 원인 분석에 한계가 있음.
  - 사용자 입력 정보에 대한 검증 메커니즘이 로그에 나타나지 않아, 문제가 발생하는 경우를 알 수 없음.

### 기대 기준선 (`infra/scenarios/06-secret-leak/expected-analysis.md`)

<details><summary>기준선 전문 펼치기</summary>

# 06 · 비밀값 포함 로그 (마스킹 검증)

| 항목 | 값 |
| --- | --- |
| 시나리오 id | `secret-leak` |
| 예상 오류 | `PaymentRetryError`, HTTP 502 — 그리고 **가짜 Bearer 토큰·이메일·카드번호** |
| 검증 대상 | **마스킹** |
| 서비스 / 환경 | `payment-api` / `staging` |
| 트리거 | `GET /debug/dump-context`, `POST /scenarios/secret-leak/burst?count=N` |

> [!caution] 이 시나리오만은 사람 눈으로 검증하지 않는다
> 다른 다섯 개는 기준선과 사람이 비교하면 충분하다. 이것은 **LLM 요청 페이로드에
> 원문 토큰이 없음을 단언하는 자동 테스트**로 만든다. 마스킹 누락은 눈으로 보면 놓치고,
> 놓치면 되돌릴 수 없다. 프로젝트 전체에서 되돌릴 수 없는 리스크는 이것 하나뿐이다.

## 여기 등장하는 값은 전부 가짜다

`infra/demo-api/app/scenarios.py` 상단에 상수로 모여 있다. 실제 자격증명을 넣지 말 것.

| 종류 | 값 | 가짜인 근거 |
| --- | --- | --- |
| Bearer / JWT | `Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.ZmFrZS1kZW1v...` | 서명 자리가 `FAKE0SIGNATURE0FOR0AILA0MASKING0DEMO0ONLY` |
| 이메일 | `demo.user@example.com`, `ops-oncall@example.org` | RFC 2606 예약 도메인 |
| 전화번호 | `010-0000-0000` | 전부 0 |
| 카드번호 | `4111-1111-1111-1111` | 업계 공용 테스트 번호 |
| API 키 | `sk-demo-FAKE000000000000000000000000000000` | 본문이 `FAKE` + 0 |
| DB 연결 문자열 | `postgresql://demo_user:not-a-real-password@db.example.invalid:5432/demo` | 비밀번호 문자열 자체가 `not-a-real-password`, RFC 6761 `.invalid` |
| 쿠키 | `session=FAKE-SESSION-COOKIE-VALUE-DEMO-ONLY` | 값이 `FAKE-...-DEMO-ONLY` |

## 새는 경로가 세 군데다 — 일부러 그렇게 만들었다

1. **`message` 본문** — `authorization=Bearer ... user_email=... card=...`
2. **`stacktrace` 본문** — 예외 문자열과 코드 라인 안에 토큰과 이메일이 박혀 있다
3. **별도 JSON 필드** — `authorization`, `contact_email`

메시지만 마스킹하면 2, 3 이 남는다. 마스킹 규칙이 **정규화 레코드 전체**(message +
stacktrace + labels/fields)에 적용되는지 확인하는 것이 이 시나리오의 핵심이다.

## 기대하는 마스킹 결과

- 위 표의 값 중 **원문 그대로 남는 것이 하나도 없어야 한다.**
- 마스킹 결과는 `<MASKED:종류>` 플레이스홀더로 대체된다 (예:
  `authorization=Bearer <MASKED:BEARER_TOKEN>`, `user_email=<MASKED:EMAIL>`,
  `card=<MASKED:CARD>`). 값을 통째로 지우기보다 **종류를 남기는 편**이 LLM 원인 파악에
  유리하다 — "토큰이 있었다"는 사실 자체가 맥락이다.
- 같은 이유로 **값 뒤의 문맥도 남긴다** (마스킹 규칙 v2). 규칙이 값을 줄 끝까지 삼키면
  `status=502` 와 예외 이름까지 같이 지워져, 원인이 다른 오류가 한 fingerprint 로 병합된다.
  이 한 줄에서 CARD·PHONE·API_KEY·DB_URI·COOKIE 규칙이 **각각 실제로 도달**하는지를
  `backend/tests/test_masking.py` 가 이 파일을 직접 읽어 단언한다 — 앞 규칙이 뒤 규칙의
  대상을 먼저 먹어 버리면 "다 지워진 것처럼" 보이면서 규칙이 조용히 빠진다.
- 마스킹된 결과만 `error_samples.masked_log` 에 저장된다.
  **원본은 DB 에 저장하지 않는다.** 원본이 필요하면 그룹의 라벨·시각으로 Loki 에서 재조회한다.
- 마스킹은 **화면 표시 전과 LLM 전송 직전에 모두** 적용된다. 경로가 다르므로
  한쪽만 걸면 다른 쪽이 새고, 그걸 컴파일러도 테스트도 잡아주지 않는다.

## 처리 순서

**마스킹 → 정규화 → fingerprint** 로 고정한다.
마스킹의 이메일·IP 치환과 정규화의 가변값 치환이 겹치므로, 순서를 고정하지 않으면
같은 로그의 fingerprint 가 마스킹 적용 여부에 따라 달라진다.

## 기대하는 그룹화 결과

- 그룹 **1 개**. 마스킹 후 메시지는 모든 발생에서 동일해지므로 (가변값이 전부
  플레이스홀더가 된다) fingerprint 가 안정적이어야 한다.
- 이 시나리오는 **마스킹이 그룹화를 도와주는** 드문 경우다. 토큰·request_id 가 남아 있으면
  발생마다 다른 그룹이 된다.

## 기대하는 LLM 분석 (원인 가설)

프롬프트에 들어가는 것은 마스킹된 로그뿐이다.

- **요약** — 결제 재시도가 업스트림에서 거부되어 502 로 실패하고 있다.
- **severity** — `high`
- **가설**
  1. 인증 토큰이 업스트림에서 거부됨 (만료·권한 부족) — 근거: 요청 헤더에 토큰이 실려 있고 502 응답
  2. 업스트림 게이트웨이 자체 오류
  3. 재시도 로직이 실패한 요청을 그대로 반복
- **확인 절차**
  1. 업스트림 응답 본문/코드 확인
  2. 재시도 정책과 백오프 설정 확인
  3. **디버깅용 컨텍스트 덤프 코드 제거** — 이게 실제로 가장 시급한 조치다
- **완화** — 요청 컨텍스트 전체를 로그에 찍는 코드 제거, 로거 레벨 조정, 구조화 로깅에서
  민감 필드 필터 적용
- **한계** — 마스킹으로 토큰 값이 가려져 있어 어떤 자격증명이 쓰였는지는 이 로그로 알 수 없다.
  (이 한계는 **의도된 것**이다. LLM 이 "토큰 값을 확인하라"고 요구하면 그건 마스킹 정책과 충돌한다)

## 자동 테스트가 단언해야 할 것

```
LLM 요청 페이로드(문자열 전체)에 아래 중 어느 것도 포함되지 않는다:
  FAKE_JWT, FAKE_EMAIL, FAKE_EMAIL_ALT, FAKE_PHONE,
  FAKE_CARD, FAKE_API_KEY, "not-a-real-password", "FAKE-SESSION-COOKIE-VALUE-DEMO-ONLY"
```

`sample-logs.jsonl` 을 fixture 입력으로 쓰면 Loki 없이도 이 단언을 돌릴 수 있다.
테스트 자체의 소유는 그룹화·마스킹 트랙(`backend/tests/test_masking*.py`)이다.

## 흔한 실패 모드

- `message` 만 마스킹하고 `stacktrace` / 추가 필드를 놓치는 것.
- 화면 표시 경로에만 마스킹을 걸고 LLM 프롬프트 조립 경로를 놓치는 것 (또는 그 반대).
- 마스킹 전 원문을 `error_samples` 에 저장해 두는 것 — LLM 전송과 별개의 유출면이 하나 더 생긴다.

</details>

### 판정 (사람 기입)

- [ ] 가설 중 하나가 기대 원인을 포함한다
- [ ] 확인 절차가 기대 절차의 핵심 단계를 담고 있다
- [ ] limitations 가 "로그만으로 확정 불가" 취지를 스스로 밝힌다
- [ ] 기대 문서의 "흔한 실패 모드"에 해당하지 않는다
- 메모:

