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
