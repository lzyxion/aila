# 구현 결정 기록

설계 문서가 정하지 않아 구현 중에 정한 것들과, 정합 리뷰가 남긴 교훈. **무엇이 언제 바뀌었는가는 git log 가 답한다** — 여기는 "왜 그쪽을 골랐는가"만 남긴다.

## Phase 이력

| 커밋 | 내용 | 검증 |
| --- | --- | --- |
| `52812bd` Phase 0 | 골격 + 공유 계약 고정 (스키마·모델·Provider ABC·라우터 스켈레톤) | 계약 테스트 32 |
| `dbc6d0b` Phase 1 | 5트랙 병렬: 인프라 / 그룹화·마스킹 / Loki 어댑터 / 정책 API / 프론트 | pytest 201 |
| `f2f5270` Phase 2 | 2트랙 병렬: LLM 어댑터 / 분석 플로우·usage·보고서 | pytest 307 |
| `dc1cefc` Phase 3 | E2E 통합 (llm-mock·`scripts/e2e_demo.py`·프론트 라이브 연동) | E2E 9단계 ALL PASS |
| `8fee831` Phase 3.5 | 정합 리뷰 반영 (high 3 · med 7 · low 8) | pytest 361 |

병렬 구현은 두 장치로 가능했다: **계약 우선**(Phase 0 이 정규화 레코드·스키마·Provider 인터페이스를 먼저 고정, 트랙은 계약 시그니처만 보고 mock 으로 개발)과 **파일 소유권**(공유 파일 동결 + 트랙별 소유 디렉터리, 소유 밖 문제는 수정 대신 보고).

## 결정과 이유

- **실행 한도 초과는 422 거절이 아니라 clamp + 경고 코드** (`range_clamped`, `limit_clamped` 등). "지난 24시간" 요청이 정책 한도에 걸려 튕기는 것보다 한도만큼 보여주는 편이 낫고, 조정 사실은 `query_runs.warnings` 에 코드로 남는다. 반대로 정책 **저장** 시점의 한도 값은 clamp 하지 않고 422 — 저장은 사람이 고칠 수 있는 순간이라 조용히 바꾸면 설정이 실제와 달라진다.
- **`openai_compatible` 은 OpenAI 어댑터로 매핑, base_url 필수** (`llm_providers/factory.py`). enum 에 값이 있는데 팩토리가 거부하는 충돌의 해소. vLLM 등 호환 엔드포인트를 별도 어댑터 없이 지원.
- **정책의 `default_range_minutes` 는 기본값이자 실행 상한을 겸한다.** "기본 1시간, 최대 6시간"이 필요해지면 전용 컬럼 + 새 revision.
- **BackgroundTasks 는 요청 세션을 쓰지 않는다** — FastAPI 0.106+ 는 yield 의존성을 백그라운드 태스크보다 먼저 정리한다. 태스크는 engine 만 물려받아 새 세션을 연다 (`analysis/service.py`).
- **멱등은 코드 체크가 아니라 DB 제약** — `analysis_jobs(fingerprint) WHERE status IN ('pending','running')` 부분 유니크 인덱스(revision 0002) + IntegrityError → 기존 작업 반환. 체크→삽입 코드는 동시 요청에 반드시 뚫린다.
- **LLM 응답 검증은 어댑터 밖 한 곳** (`parse_analysis_result`). 어댑터는 원시 dict + 토큰 수만 반환. strict 스키마 변환에서 제거된 값 제약도 같은 지점이 강제한다.
- **llm-mock 을 인프라에 포함** — 요청의 json_schema 로 응답을 생성하고 `/debug/last-request` 로 수신 페이로드를 노출. API 키 없이 E2E 가 돌고, "LLM 요청 페이로드에 원문 비밀값이 없음" 단언이 실제 전송 경로에서 성립한다.

## 정합 리뷰 결함과 교훈 (Phase 3.5 에서 전부 수정)

| 심각도 | 결함 | 수정 |
| --- | --- | --- |
| high | 마스킹이 줄 끝까지 삼켜 504 Timeout 과 401 Auth 가 같은 fingerprint 로 병합 | 값 패턴 공백 경계 축소, 규칙 v2 |
| high | 서비스별 건수가 metric 라벨 유실로 잘린 라인 집계에 조용히 폴백 | `sum by (라벨)` + 폴백 경고 코드 |
| high | 동시 분석 요청에 멱등·일일 한도 뚫림 (중복 과금) | 부분 유니크 인덱스 + 잠금 |
| med | Python 트레이스백 "상위 프레임"이 반대(진입점) 프레임 | 형식별 분기, 정규화 v2 |
| med | 보존 기간·단가표가 선언만 있고 삭제 경로·쓰기 API 부재 | purge 경로 + `/api/settings` + 시드 |
| med | base_url 내장 자격증명이 오류 메시지로 DB·응답에 유출 | 오류 텍스트 저장 경로 전부 mask |

**교훈**: 세 high 의 공통점은 개별 트랙 테스트로는 안 잡힌다는 것 — 결합(마스킹×그룹화), 조용한 폴백(실패가 아닌 성공), 경합(단일 요청 밖). 병렬 구현 뒤에는 결합·부재·경합을 노리는 별도 리뷰 단계가 필수다.

## Phase 4 — 1차 사용 피드백 (백엔드 몫)

- **모델 목록 조회는 어댑터 인스턴스가 아니라 classmethod** (`OpenAIProvider.list_models` / `AnthropicProvider.list_models`). 모델을 *고르기 전에* 필요한 조회라 `model` 이 필수인 생성자를 태울 수 없다. `LLMProvider` ABC 의 연산은 `test_connection`·`analyze` 둘뿐이라는 어댑터 계약은 그대로 뒀다 — 목록 조회는 인스턴스 연산이 아니라 엔드포인트 조회다. 프로바이더 분기는 `factory.list_models` 한 곳(=`build_llm_provider_from_values` 와 같은 지원 판정·`openai_compatible` base_url 필수 규칙)에 모았다.
- **`POST /api/llm-connections/models` — 조회지만 GET 이 아니다.** 처음에는 `api_key` 를 쿼리스트링으로 받는 GET 이었는데, 그러면 평문 키가 서버 액세스 로그·프록시 로그·브라우저 히스토리에 남는다. **비밀을 실어 보내는 요청은 조회여도 바디로 받는다** (`LLMModelListRequest`). GET 라우트는 남기지 않았다 — 하위 호환을 위해 유출 경로를 살려 두면 그 경로로만 계속 호출된다.
- **`/models` 는 `/{connection_id}` 보다 위에 등록한다.** 아래로 내려가면 `"models"` 가 경로 파라미터로 먹혀 422 가 난다. 실패는 프로바이더 사유면 502, 입력 오류면 400 이고 `detail` 은 마스킹을 거친다(평문 키·base_url 내장 자격증명 유출 차단). 프론트는 실패 시 자유 입력으로 폴백하므로 `detail` 에 **원인**을 남기는 것이 계약의 핵심이다.
- **`provider` 는 바디에서도 열거형이 아니라 `str` 로 받는다.** 모르는 이름을 pydantic 이 422 로 튕기면 프론트의 `isEndpointMissing` 이 "경로 없음"으로 오해해 원인 없이 폴백이 켜진다. 라우터에서 **사유가 담긴 400** 으로 바꾼다. (바디로 옮기면서 `connection_id=` 빈 값을 정수로 파싱해 주던 헬퍼는 사라졌다 — JSON 은 `null`·생략으로 "지정 안 함"을 표현한다.)
- **실행 이력 목록의 정렬은 `started_at DESC, id DESC`.** `started_at` 만으로는 같은 초에 들어간 두 건의 순서가 흔들려 페이지 경계에서 행이 중복되거나 사라진다. `group_count` 는 페이지 행에 대해 **한 번의 group-by** 로 센다(행마다 count 쿼리 금지).
- **일일 한도의 "하루" 는 UTC 자정 → `app_settings.timezone` 의 로컬 자정.** UTC 기준이면 한국에서는 업무 시작 시각인 오전 9 시에 카운터가 리셋돼 "어제 저녁에 쓴 분량이 아침까지 남는다". 로컬 자정을 먼저 구하고 UTC 로 변환하는 **순서**가 핵심이다(반대로 하면 9 시간 어긋난다). 타임존은 쓰기 시점에 `zoneinfo` 로 실제 로드해 검증한다 — 오타를 받아 두면 비용 차단 장치가 판정 시점에 조용히 멈춘다. 읽기 경로도 로드 실패 시 기본값으로 내려간다(예외로 분석이 막히지도, 무제한이 되지도 않게).
- **revision 0003 은 데이터 전용**(스키마 변경 없음): `timezone` 행 시드 + `daily_analysis_limit` 설명 문구 갱신. 저장된 description 이 in-code 문구보다 우선하므로, 0002 를 이미 돌린 DB 는 이 revision 없이는 "UTC 자정 기준" 을 계속 보여 준다.

## 운영 노트

- **모델 단가표는 `PUT /api/settings/model_pricing` 로 관리한다** (사용량 화면의 인라인 등록 폼도 같은 API). 미등록 모델의 추정 비용은 `-` 로 표시된다 (0 이 아님 — 의도). 프로바이더 API 는 단가를 제공하지 않으므로 자동 수집은 불가능하다.
- **일일 분석 한도는 `app_settings.timezone` 의 로컬 자정 기준** 리셋 (기본 `Asia/Seoul`). 전역 기본 50, `PUT /api/settings/daily_analysis_limit` · `PUT /api/settings/timezone`.
- **전송 페이로드 감사는 llm-mock 연결에서만 가능** — 실 프로바이더에는 `/debug/last-request` 가 없다. 마스킹 회귀 검증은 llm-mock 으로.
- **compose 의 Fernet 키는 데모용 평문이다.** 교체하면 기존 저장 secret 복호화 불가 → 연결 재등록.
- frontend 컨테이너는 소스를 COPY 하므로 수정 후 `--build frontend` 필요.
- `scripts/e2e_demo.py` 는 실행마다 일일 한도 2건 소모.

## 다음 단계

- 실 LLM 키로 시나리오 6종을 분석해 `infra/scenarios/*/expected-analysis.md` 기준선과 비교 — 프롬프트 품질 첫 실측.
- 이후 확장은 설계 문서의 "향후 확장" 절 (Redis 워커, Slack 연동, 추가 LogSourceProvider 등).
