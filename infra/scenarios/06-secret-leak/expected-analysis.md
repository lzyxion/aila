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
- 마스킹 결과는 플레이스홀더로 대체된다 (예: `authorization=<BEARER_TOKEN>`,
  `user_email=<EMAIL>`, `card=<CARD>`). 값을 통째로 지우기보다 **종류를 남기는 편**이
  LLM 원인 파악에 유리하다 — "토큰이 있었다"는 사실 자체가 맥락이다.
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
