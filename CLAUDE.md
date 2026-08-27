# AILA — Loki 기반 AI 로그 분석기

Loki 에 쌓인 오류 로그를 정책으로 조회하고, 유사 오류를 그룹으로 묶어 **마스킹된 대표 로그만** LLM 에 넘겨 원인 가설·확인 절차·대응 초안을 받는 도구. 중심 원칙은 하나 — "LLM 에 넣는 양을 정책과 그룹화로 먼저 줄인다". 비용·민감정보·환각 리스크가 전부 이 축에서 갈린다.

## 문서 지도

| 문서 | 내용 |
| --- | --- |
| `README.md` | Quickstart, 디렉터리 구조, **계약상 제약(모든 변경에 적용되는 원본 목록)** |
| `docs/DECISIONS.md` | 구현 중 내린 결정과 이유, 정합 리뷰 교훈, 운영 노트 |
| `infra/scenarios/*/expected-analysis.md` | 장애 시나리오 6종의 기대 분석 기준선 |
| 설계 원문 | `~\Documents\Obsidian\Projects\Loki기반 AI 로그 분석기\Loki 기반 AI 로그 분석기 설계.md` |

## 명령어

```powershell
# backend 테스트 (반드시 backend/ 에서)
cd backend; .\.venv\Scripts\python.exe -m pytest

# backend 개발 서버 / 마이그레이션 (AILA_DATABASE_URL 필요)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
.\.venv\Scripts\python.exe -m alembic upgrade head

# frontend (frontend/ 에서)
npm run build        # tsc --noEmit + vite build
npm run smoke        # mock 계층 계약 단언

# 전체 데모 스택 + E2E
docker compose --profile app up -d --build
python scripts/e2e_demo.py    # 9단계 ALL PASS 가 정상
```

## 구조 (backend/app/)

`schemas/`(정규화 레코드·분석 결과·API 모델 — 공유 계약) · `providers/`(LogSourceProvider·LLMProvider ABC) · `loki/`(Loki 어댑터, `build_provider`) · `llm_providers/`(OpenAI·Anthropic 어댑터, `build_llm_provider`) · `grouping/`+`masking/`(마스킹→정규화→fingerprint, 순수 함수) · `policies/`(정책 CRUD·query-run 실행) · `error_groups/` · `analysis/`(작업 생성·BackgroundTasks 실행·보고서) · `dashboard/`(정책 상세 `overview` + 전체 요약 `summary` + 전 정책 오류 그룹 `error-groups` — 접기·회차 COUNT 규칙은 `counting.py` 한 곳) · `usage/`(집계 + `daily-limit` 게이지 — 한도 검사와 같은 `daily_usage` 재사용) · `app_settings/`(전역 설정 키 4종) · `connections/`+`llm_connections/`(연결 CRUD) · `auth/`(계정 CRUD·세션·전역 보호 의존성 — 마지막 admin 보호·세션 무효화는 `auth/service.py`) · `scheduler/`(60초 tick — 정책 주기 실행 + 신규 fingerprint 자동 분석)

트랙 간 결합은 `*/integrations.py` 의 지연 import 로만 한다 — 테스트 mock 지점도 거기다.

## 작업 규칙

- **README 의 "계약상 제약" 절이 모든 변경의 상위 규칙이다.** 특히: 원본(마스킹 전) 로그를 DB 에 저장하는 코드를 절대 만들지 않는다 / LLM 전송 직전 마스킹을 우회하는 경로를 만들지 않는다 / **분석 자동 트리거는 정책의 `auto_analyze_new` 하나뿐이며 그 밖의 자동 실행(임계치·재분석·알림 연동)을 추가하지 않는다.** 수집 중단 확인(`ingest_absent`, Phase 7)도 조회 실행에 얹힌 **경고 기록**일 뿐이다 — 여기에 알림·자동 대응을 붙이는 것이 정확히 이 규칙이 막는 일이다.
- **자동 분석의 유일한 진입점은 `analysis.service.create_analysis_job` 이다.** 스케줄러도 이 함수를 탄다 — `allow_ai_analysis`·멱등(부분 유니크 인덱스)·일일 한도(429)를 우회하는 별도 실행 경로를 만들지 않는다. 대상은 **fingerprint 분석 이력이 전혀 없는 그룹**으로 한정한다(실패 이력도 "이력 있음"이다 — 아니면 같은 실패를 매 회차 다시 태운다).
- **`/api/**` 는 전역 의존성(`auth.dependencies.enforce_api_auth`)으로 보호된다.** 새 라우터에 인증을 따로 붙이지 않는다(기본이 "닫힘"이다). 예외를 늘리려면 `PUBLIC_API_PATHS` 를 고쳐야 하고, 그건 구멍을 뚫는 일이므로 이유를 DECISIONS 에 남긴다. viewer 는 GET 만 가능하다.
- **스케줄러는 단일 uvicorn 워커 전제다.** 겹침 방지가 in-process 락이라 워커를 늘리면 같은 정책이 중복 실행된다(조용히 동작하는 것처럼 보인다).
- **공유 계약 파일**(`schemas/**`, `providers/**`, `models.py`, `main.py`)을 고치면 파급을 확인한다: `tests/test_contracts.py` + 프론트 `frontend/src/api/types.ts` 는 스키마의 **손 사본**이라 함께 갱신해야 한다. 스키마는 추가·완화 위주로, 기존 필드 변경은 신중히.
- **마스킹·정규화 규칙을 바꾸면 버전 상수를 올린다** (`MASKING_RULE_VERSION` / `NORMALIZATION_RULE_VERSION`). 규칙 변경 후 그룹 구성 변화를 추적하는 유일한 수단이다. 마스킹은 약화 금지 — 과잉만 줄인다(뒤 문맥 보존).
- **DB 스키마 변경은 기존 revision 을 고치지 말고 새 Alembic revision** + `models.py` 동기화.
- **완료 기준**: backend 전체 pytest + (프론트 변경 시) `npm run build`·`npm run smoke` 통과. 조회·분석 경로를 바꿨으면 `scripts/e2e_demo.py` 까지.
- 테스트에서 실제 LLM·Loki 를 호출하지 않는다 — `integrations` mock 과 `tests/test_policies_fixtures.py` 의 `no_real_log_source`(모듈에서 이름으로 import 해야 적용됨)를 쓴다.
- **테스트 클라이언트는 기본이 admin 세션이다** (`tests/conftest.py` 의 `_default_admin_session` 이 `auth.dependencies.identity_from_request` 를 대체한다). 인증·권한 자체를 검증하는 테스트만 `@pytest.mark.real_auth` 로 실제 경로를 탄다 — 새 테스트에 로그인 절차를 넣을 필요가 없다.
- 이 스택은 **로컬·데모 전용**이다. 세션 쿠키 인증은 있지만 기본 계정이 `admin/admin` 이고 쿠키는 `secure=false` 다 — 외부 노출 전에 `AILA_ADMIN_PASSWORD`·`AILA_SESSION_COOKIE_SECURE` 부터 바꾼다.
