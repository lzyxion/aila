# infra/scenarios/

장애 시나리오 6 종의 **기대 결과 기준선**이다.

LLM 결과를 자동 채점하지는 않는다. 하지만 프롬프트나 정규화 규칙을 고친 뒤
결과가 나아졌는지 사람이 비교할 대상이 없으면 개선을 판단할 수 없다.
그래서 각 시나리오에 "이런 원인 가설과 확인 절차가 나오면 잘 된 것"을 적어 둔다.

**예외는 06 하나다.** 마스킹은 사람 눈으로 검증하지 않는다 —
LLM 요청 페이로드에 원문 토큰이 없음을 단언하는 자동 테스트로 만든다.

| # | 디렉터리 | id | 예상 오류 | 검증 대상 |
| --- | --- | --- | --- | --- |
| 1 | `01-payment-timeout/` | `payment-timeout` | `TimeoutError`, 504 | 빈도 급증 감지·LLM 원인 가설 |
| 2 | `02-db-connection/` | `db-connection` | `DatabaseConnectionError` | 오류 그룹화·대응 절차 |
| 3 | `03-auth-expired/` | `auth-expired` | 401, `JWT expired` | 서비스·기간 필터 |
| 4 | `04-null-reference/` | `null-reference` | stack trace | 스택 프레임 추출 |
| 5 | `05-post-deploy-spike/` | `post-deploy-spike` | `release` 라벨 변화 | 배포 전후 비교 |
| 6 | `06-secret-leak/` | `secret-leak` | Bearer token, email | **마스킹 (자동 테스트 필수)** |

## 각 디렉터리의 파일

- `expected-analysis.md` — 재현 방법, 기대 그룹화 결과, 기대 LLM 분석, 흔한 실패 모드
- `sample-logs.jsonl` — 실제로 Loki 에 들어간 로그 라인을 그대로 뽑은 것.
  Loki 없이 파서·정규화·마스킹을 돌려볼 때 입력으로 쓴다.

`unstructured-samples.log` 는 시나리오에 속하지 않는 **비정형 라인 모음**이다.
`| json` 이 실패하는 네 가지 전형(uvicorn access log, 레거시 포맷터, Java 스택 조각,
로그 드라이버에 잘린 JSON)이 들어 있다.

> `sample-logs.jsonl` / `unstructured-samples.log` 는 인프라 트랙이 만든 **참고 자료**다.
> pytest fixture 로 쓸지, 복사해서 쓸지는 그룹화·마스킹 트랙이 정한다
> (`backend/tests/**` 는 인프라 트랙 소유가 아니다).

## 다시 뽑는 방법

demo-api 를 고쳐 로그 모양이 바뀌면 샘플도 다시 뽑는다.

```powershell
docker compose up -d
# 6 종을 모두 한 번씩 터뜨린다
curl.exe "http://localhost:8081/payment/charge"
curl.exe "http://localhost:8081/orders"
curl.exe "http://localhost:8081/auth/me"
curl.exe "http://localhost:8081/profile"
curl.exe "http://localhost:8081/debug/dump-context"
curl.exe -X POST "http://localhost:8081/admin/deploy?release=v1.5.0"
# 그 뒤 Loki 에서 `| json | scenario="<id>"` 로 뽑아 저장한다
```
