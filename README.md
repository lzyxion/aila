# AILA — Loki 기반 AI 로그 분석기

Loki 에 이미 쌓여 있는 애플리케이션 오류 로그를 운영자가 정의한 정책으로 조회하고,
유사 오류를 하나의 그룹으로 묶은 뒤 그 **대표 로그만** LLM 에 넘겨 원인 가설·확인 절차·
대응 초안을 한국어로 받는 웹 도구다. Grafana/Loki 를 대체하는 로그 저장소도 범용 관측성
플랫폼도 아니고, 기존 Loki 위에 얹는 AI 운영 레이어다. 수집과 저장은 Alloy 와 Loki 가
이미 하고 있으므로 건드리지 않는다 — 이 도구는 Loki 를 **읽기만 한다.**

설계의 중심축은 "LLM 에 넣는 양을 정책과 그룹화로 먼저 줄인다"는 한 가지 원칙이고,
비용·민감정보·환각이라는 세 가지 위험이 모두 이 축에서 갈린다. 그래서 분석은 **수동
트리거**만 두고, 기간·라인 수 상한과 일일 분석 횟수 상한을 서버에서 강제하며, 마스킹은
화면 표시 전과 LLM 전송 직전에 **이중으로** 걸고, 마스킹 전 원본 로그는 DB 에 저장하지
않는다. LLM 응답은 `hypotheses` 와 `limitations` 를 필수로 갖는 스키마로 받아 단정을
구조적으로 막는다. 되돌릴 수 없는 위험은 민감정보 유출 하나뿐이므로, 우선순위를 다툴 일이
생기면 마스킹을 먼저 완성한다.

> 설계 원문: `Loki 기반 AI 로그 분석기 설계.md` (Obsidian vault)
> MVP 에는 인증이 없다. 배포 대상은 **로컬·데모 환경으로 한정**한다.

---

## Quickstart — 데모 재현 (처음부터)

필요한 것은 **Docker Desktop 하나**다. LLM API 키도, 외부 네트워크도 필요 없다 —
분석 단계는 `infra/llm-mock`(OpenAI 호환 스텁)이 받는다.

```powershell
cd aila

# 1) 전체 스택 (postgres·loki·alloy·grafana·demo-api·llm-mock·backend·frontend)
docker compose --profile app up -d --build

# 2) 스키마 적용 — 기동 시 자동으로 돌리지 않는다 (스키마 변경 시점은 사람이 통제한다)
docker compose --profile app exec backend alembic upgrade head
```

떠 있는지 확인:

| | 주소 |
| --- | --- |
| 제품 UI | <http://localhost:5173> |
| 백엔드 API 문서 | <http://localhost:8000/docs> |
| Grafana (Loki 유입 육안 확인) | <http://localhost:3000> |
| demo-api (시나리오 트리거) | <http://localhost:8081/scenarios> |
| llm-mock (전송 페이로드 감사) | <http://localhost:8090/debug/last-request> |

### 3-A. 스크립트로 한 번에 (권장)

```powershell
backend\.venv\Scripts\python.exe scripts\e2e_demo.py
```

연결 등록 → 시나리오 6 종 트리거 → 정책 미리보기·실행 → 그룹화 → **마스킹 검증** →
AI 분석 → **전송 페이로드 감사** → 보고서·사용량·대시보드까지 한 번에 돌리고
마지막에 `ALL PASS` 를 찍는다. **멱등**하므로 몇 번을 다시 돌려도 된다.
실패하면 어느 단계에서 무엇이 어긋났는지 출력하고 비0 으로 종료한다.

httpx 만 있으면 되므로 백엔드 가상환경의 python 으로 도는 게 가장 간단하다
(다른 python 이면 `pip install httpx`). 백엔드를 컨테이너가 아니라 호스트에서
돌리는 중이라면 `--loki-url http://localhost:3100 --llm-base-url http://localhost:8090/v1`.

이 스크립트가 증명하는 것 중 하나는 눈으로는 확인할 수 없다 —
시나리오 06 의 가짜 비밀값이 **화면 응답과 LLM 요청 본문 양쪽 모두**에 남아 있지
않다는 단언이다. 그래서 llm-mock 이 자기가 받은 요청을 `/debug/last-request` 로
되돌려준다 (진짜 프로바이더로는 관측할 수 없는 경로다).

### 3-B. UI 로 손으로 (같은 순서)

1. **LLM 연결** 화면 → 새 연결
   `provider=OpenAI 호환`, `model=llm-mock-1`, `base URL=http://llm-mock:8000/v1`,
   `기본 연결로 지정` 체크 → **연결 테스트** → 저장.
   (Loki 연결은 `base_url=http://loki:3100` 으로 같은 방식. 백엔드가 컨테이너 안에
   있으므로 `localhost` 가 아니라 **서비스명**을 쓴다.)
2. 장애를 하나 터뜨린다 — 시나리오 목록과 트리거 방법은 `infra/README.md`.
   ```powershell
   curl.exe "http://localhost:8081/debug/dump-context"                     # 06 비밀값
   curl.exe -X POST "http://localhost:8081/scenarios/payment-timeout/burst?count=120"
   ```
   demo-api 는 뜨는 즉시 백그라운드 트래픽도 만들므로 아무것도 하지 않아도 데이터는 쌓인다.
3. **분석 정책** 화면 → LogQL `{service="payment-api"} | json | level="ERROR"` →
   **저장 전 미리보기**로 실제로 무엇이 잡히는지 확인 → 저장.
4. **대시보드** → 정책 선택 → `정책 실행` → 상위 오류 그룹에서 하나 클릭.
5. 그룹 상세에서 대표 로그가 `<MASKED:...>` 로 치환된 것을 확인하고 → `AI 분석 실행`.
   결과에는 **원인 가설**과 **한계**가 반드시 함께 나온다 (스키마가 단정을 막는다).
6. **분석 이력·사용량** 화면에서 토큰·추정 비용을 확인한다.

### 정리

```powershell
docker compose --profile app down       # 정지 (볼륨 유지 — 다시 up 하면 데이터가 남아 있다)
docker compose --profile app down -v    # 로그·DB 까지 삭제하고 처음부터
```

> **실제 LLM(OpenAI·Anthropic 키)으로 바꾸려면** — compose 를 고칠 필요 없이 연결
> 하나만 새로 만들면 된다. provider·model·base_url 조합표와 주의점은
> `infra/README.md` 의 "실제 LLM 으로 바꾸기" 절에 있다.

---

## 디렉터리 구조

```
aila/
├── README.md
├── .gitignore
├── docker-compose.yml            # 데모 스택 전체 (profile "app" 에 backend·frontend)
├── scripts/
│   └── e2e_demo.py               # 전 경로 E2E — 마스킹·전송 페이로드 감사 포함
├── infra/                        # Loki·Alloy·Grafana·demo-api·llm-mock, 장애 시나리오
│   ├── README.md
│   └── llm-mock/                 # OpenAI 호환 스텁 + /debug/last-request (감사 지점)
├── frontend/                     # React + TypeScript + Vite
│   └── README.md
└── backend/
    ├── pyproject.toml            # [freeze] 의존성 선언
    ├── alembic.ini               # ASCII only (Windows cp949 이슈)
    ├── .env.example
    ├── alembic/
    │   ├── env.py
    │   ├── versions/0001_initial_schema.py   # [freeze] models.py 와 1:1
    │   └── versions/0002_active_job_guard_and_settings_seed.py
    │                             #   진행 중 분석 작업 부분 유니크 인덱스 + 설정 기본값 시드
    ├── app/
    │   ├── main.py               # [freeze] 라우터 include 지점
    │   ├── config.py             # [freeze] AILA_* 환경변수
    │   ├── db.py                 # [freeze] Base / engine / get_db
    │   ├── models.py             # [freeze] 전체 테이블
    │   ├── enums.py              # [freeze] 도메인 열거형
    │   ├── crypto.py             # [freeze] Fernet 암복호화
    │   ├── stub.py               # 501 헬퍼 (Phase 1 진행 중 사라진다)
    │   ├── schemas/              # [freeze]
    │   │   ├── logrecord.py      #   정규화 로그 레코드 · FetchResult · TimeRange
    │   │   ├── analysis.py       #   LLM 구조화 응답 스키마
    │   │   └── api.py            #   REST 요청/응답 모델
    │   ├── providers/            # [freeze]
    │   │   ├── logsource.py      #   LogSourceProvider ABC
    │   │   └── llm.py            #   LLMProvider ABC
    │   ├── connections/router.py     # /api/loki-connections
    │   ├── llm_connections/router.py # /api/llm-connections
    │   ├── policies/router.py        # /api/policies, /api/query-runs
    │   ├── error_groups/router.py    # /api/query-runs/{id}/error-groups, /api/error-groups
    │   ├── analysis/router.py        # /api/analysis-jobs (+ report)
    │   ├── dashboard/router.py       # /api/dashboard/overview
    │   ├── usage/router.py           # /api/usage
    │   ├── app_settings/router.py    # /api/settings (예약 3 종 화이트리스트)
    │   │                             #   + /api/maintenance/purge-samples (policies/router.py)
    │   ├── loki/                 # (빈 구현) Loki 어댑터
    │   ├── grouping/             # (빈 구현) 파싱·정규화·fingerprint
    │   └── masking/              # (빈 구현) 민감정보 마스킹
    └── tests/test_contracts.py   # [freeze] Phase 0 계약 테스트
```

---

## 개발 환경

```powershell
# 백엔드
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest          # backend/ 에서 실행해야 한다
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload

# 마이그레이션 (AILA_DATABASE_URL 필요)
.\.venv\Scripts\python.exe -m alembic upgrade head
```

`.env` 는 `backend/.env.example` 를 복사해 만든다. `AILA_ENCRYPTION_KEY` 가 없으면
앱은 뜨지만 연결 secret 암복호화 시점에 실패한다.

---

## Phase 1 트랙별 파일 소유권

병렬 작업 중 **자기 소유가 아닌 파일을 수정하지 않는다.** 소유 밖의 파일에 문제가 있으면
직접 고치지 말고 보고한다.

| 트랙 | 소유 파일 |
| --- | --- |
| **인프라** | `docker-compose.yml`, `infra/**` |
| **그룹화·마스킹** | `backend/app/grouping/**`, `backend/app/masking/**`, `backend/tests/test_grouping*.py`, `backend/tests/test_masking*.py`, 관련 fixture |
| **Loki 어댑터** | `backend/app/loki/**`, `backend/app/connections/**`, `backend/tests/test_loki*.py` |
| **정책 API** | `backend/app/policies/**`, `backend/app/error_groups/**`, `backend/app/dashboard/**`, `backend/tests/test_policies*.py`, `backend/tests/test_error_groups*.py` |
| **프론트** | `frontend/**` |

> **LLM 분석**(`backend/app/analysis/**`, `backend/app/llm_connections/**`, `backend/app/usage/**`)은
> 설계 문서 일정상 3 주차 항목이라 Phase 1 5 개 트랙에는 포함되지 않는다. 라우터 스켈레톤과
> `LLMProvider` 계약만 Phase 0 에 들어 있다.

### 동결(freeze) — 수정 금지

아래 파일은 모든 트랙이 공유하는 계약이다. **고치지 말고, 문제를 발견하면 보고한다.**

- `backend/app/main.py`
- `backend/app/models.py`, `backend/app/enums.py`, `backend/app/db.py`, `backend/app/config.py`, `backend/app/crypto.py`
- `backend/app/schemas/**` (`logrecord.py`, `analysis.py`, `api.py`)
- `backend/app/providers/**` (`logsource.py`, `llm.py`)
- `backend/alembic/versions/0001_initial_schema.py`
- `backend/pyproject.toml`
- `backend/tests/test_contracts.py`

> 설계-구현 정합 리뷰에서 동결 파일 세 곳을 **최소 범위로** 열었다 (전부 추가·완화):
> `main.py` 에 `/api/settings`·`/api/maintenance` include 두 줄, `models.py` 의
> `AnalysisJob.__table_args__` 에 진행 중 작업 부분 유니크 인덱스 하나(= revision `0002`),
> `schemas/api.py` 에 신규 응답 모델과 필드 추가(기존 필드 제거 없음).

새 마이그레이션이 필요하면 `0001` 을 고치지 말고 새 revision 을 추가한 뒤 보고한다.
라우터 스켈레톤(`*/router.py`)은 각 트랙이 자기 것만 채운다 — 시그니처와 `response_model`
은 유지하고 본문만 구현한다.

---

## 계약상 제약 (전 트랙 공통)

- 원본(마스킹 전) 로그는 **DB 에 저장하지 않는다.** `error_samples.masked_log` 만 있다.
- 처리 순서는 **마스킹 → 정규화 → fingerprint** 로 고정한다.
- 마스킹은 **화면 표시 전과 LLM 전송 직전에 모두** 적용한다.
- 기간·라인 수 상한, 일일 분석 한도는 **서버가** 강제한다 (UI 제한은 우회된다).
- 건수·추이는 로그 라인을 세지 않고 `count_over_time` metric 쿼리로 구한다.
- 분석 상태 표시·중복 판정은 그룹 id 가 아니라 **fingerprint 기준**이다.
- LLM 응답 Pydantic 검증은 **어댑터 밖 공통 경로에서 한 번만** 한다.
- 분석은 수동 트리거만 존재한다. 자동 실행을 추가하지 않는다.
- `estimated_cost` 는 추정이다. 화면에서 "추정" 표기를 유지한다.
  단가표에 모델이 없으면 값은 **`null` 이며 0 이 아니다** — 0 으로 적으면 "쌌다"로 읽힌다.
  항목 전부가 `null` 이면 합계(`total_estimated_cost`)도 `null` 이고, 화면은 `-` 로 쓴다.

### 한도의 기준 두 가지 (자주 어긋나는 지점)

- **일일 분석 한도는 UTC 자정 기준이다.** 전역(`app_settings.daily_analysis_limit`)과
  정책별(`analysis_policies.daily_analysis_limit`) 모두 `analysis_jobs.requested_at` 이
  "오늘 00:00 UTC 이후"인 건수를 센다. 서버가 KST 로 돌아도 한도는 UTC 자정에 리셋되므로,
  한국 시간 오전 9 시에 카운터가 0 으로 돌아간다. 로컬 자정을 기대하고 있으면 어긋난다.
- **정책의 `default_range_minutes` 는 기본값이자 실행 상한이다.** 이름은 "기본"이지만
  `POST /policies/{id}/query-runs` 가 기간을 명시해도 이 값(과 서버 상한
  `AILA_MAX_QUERY_RANGE_MINUTES` 중 작은 쪽)으로 **clamp** 한다. 422 로 튕기지 않고
  조용히 줄이지도 않는다 — 조정 사실은 `query_runs.warnings` 에 `range_clamped` 로 남는다.
  더 넓은 기간을 조회하려면 정책의 값을 올려야 한다.
