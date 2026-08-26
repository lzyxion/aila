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

`schemas/`(정규화 레코드·분석 결과·API 모델 — 공유 계약) · `providers/`(LogSourceProvider·LLMProvider ABC) · `loki/`(Loki 어댑터, `build_provider`) · `llm_providers/`(OpenAI·Anthropic 어댑터, `build_llm_provider`) · `grouping/`+`masking/`(마스킹→정규화→fingerprint, 순수 함수) · `policies/`(정책 CRUD·query-run 실행) · `error_groups/` · `analysis/`(작업 생성·BackgroundTasks 실행·보고서) · `dashboard/` · `usage/` · `app_settings/`(전역 설정 키 3종) · `connections/`+`llm_connections/`(연결 CRUD)

트랙 간 결합은 `*/integrations.py` 의 지연 import 로만 한다 — 테스트 mock 지점도 거기다.

## 작업 규칙

- **README 의 "계약상 제약" 절이 모든 변경의 상위 규칙이다.** 특히: 원본(마스킹 전) 로그를 DB 에 저장하는 코드를 절대 만들지 않는다 / LLM 전송 직전 마스킹을 우회하는 경로를 만들지 않는다 / 분석 자동 트리거를 추가하지 않는다.
- **공유 계약 파일**(`schemas/**`, `providers/**`, `models.py`, `main.py`)을 고치면 파급을 확인한다: `tests/test_contracts.py` + 프론트 `frontend/src/api/types.ts` 는 스키마의 **손 사본**이라 함께 갱신해야 한다. 스키마는 추가·완화 위주로, 기존 필드 변경은 신중히.
- **마스킹·정규화 규칙을 바꾸면 버전 상수를 올린다** (`MASKING_RULE_VERSION` / `NORMALIZATION_RULE_VERSION`). 규칙 변경 후 그룹 구성 변화를 추적하는 유일한 수단이다. 마스킹은 약화 금지 — 과잉만 줄인다(뒤 문맥 보존).
- **DB 스키마 변경은 기존 revision 을 고치지 말고 새 Alembic revision** + `models.py` 동기화.
- **완료 기준**: backend 전체 pytest + (프론트 변경 시) `npm run build`·`npm run smoke` 통과. 조회·분석 경로를 바꿨으면 `scripts/e2e_demo.py` 까지.
- 테스트에서 실제 LLM·Loki 를 호출하지 않는다 — `integrations` mock 과 `tests/test_policies_fixtures.py` 의 `no_real_log_source`(모듈에서 이름으로 import 해야 적용됨)를 쓴다.
- 이 스택은 **로컬·데모 전용**이다(인증 없음). 외부 노출 전제의 기능을 추가하려면 인증부터.
