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
