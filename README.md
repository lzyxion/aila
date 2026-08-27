# AILA — AI 로그 분석기

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

> 설계 원문: `AILA 설계.md` (Obsidian vault, `Projects/AILA/`)
> `/api/**` 는 세션 쿠키 인증으로 막혀 있지만(Phase 5), 기본 계정이 `admin/admin`
> 이고 쿠키가 `secure=false` 다. 배포 대상은 여전히 **로컬·데모 환경으로 한정**한다.

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

# 3) 백엔드를 한 번 재기동한다 — 관리자 계정은 기동 시 시드된다
#    (users 테이블이 생기기 전에 뜬 컨테이너는 시드를 건너뛰고 경고만 남긴다)
docker compose --profile app restart backend
```

> ⚠️ **기본 관리자 계정은 `admin` / `admin` 이다.** 저장소에 그대로 적힌 데모용
> 값이고, 이 스택을 로컬 밖에 노출한다면 **반드시 바꾼다**. 저장소 루트 `.env` 에
> `AILA_ADMIN_USERNAME` / `AILA_ADMIN_PASSWORD` 를 넣으면 된다(커밋 금지).
> 이미 만들어진 계정의 비밀번호는 env 를 바꿔도 되돌아가지 **않는다** — 재기동마다
> 기본값으로 리셋되면 운영자가 바꾼 값이 매번 사라지기 때문이다. 그 경우
> `POST /api/auth/users` 로 새 admin 을 만들고 예전 계정을 쓰지 않으면 된다.

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

0. **로그인** — `admin` / `admin` (위 경고 참고). 세션이 없으면 화면은 `/login` 으로
   돌아간다(백엔드가 `/api/**` 에 401 을 주고 프런트가 그것을 가로챈다).
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
    │   ├── versions/0002_active_job_guard_and_settings_seed.py
    │   │                         #   진행 중 분석 작업 부분 유니크 인덱스 + 설정 기본값 시드
    │   ├── versions/0003_timezone_setting.py
    │   │                         #   app_settings 예약 키 timezone 시드 (스키마 변경 없음)
    │   ├── versions/0004_auth_and_scheduling.py
    │   │                         #   users·user_sessions + 정책 스케줄 필드 + triggered_by
    │   └── versions/0005_baseline_and_expected_services.py
    │                             #   정책 baseline_query + 연결 expected_services (Phase 7)
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
    │   ├── connections/router.py     # /api/log-source-connections
    │   ├── llm_connections/router.py # /api/llm-connections
    │   ├── policies/router.py        # /api/policies, /api/query-runs
    │   ├── error_groups/router.py    # /api/query-runs/{id}/error-groups, /api/error-groups
    │   ├── analysis/router.py        # /api/analysis-jobs (+ report)
    │   ├── dashboard/router.py       # /api/dashboard/overview (정책 상세)
    │   │                             #   + /api/dashboard/summary (정책 전체 요약)
    │   │                             #   counting.py — 접기·회차 COUNT 공용 규칙 (Phase 7)
    │   ├── auth/                     # /api/auth/login|logout|me|users
    │   │                             #   passwords(scrypt) · service(세션) · dependencies(전역 보호)
    │   ├── scheduler/                # 60 초 tick — 정책 주기 실행 + 신규 fingerprint 자동 분석
    │   ├── usage/router.py           # /api/usage + /api/usage/daily-limit (한도 게이지)
    │   ├── app_settings/router.py    # /api/settings (예약 4 종 화이트리스트)
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
- 분석 트리거는 **수동이 기본**이고, 유일한 예외가 정책의 `auto_analyze_new` 다
  (Phase 5). 그 경우에도 대상은 **fingerprint 분석 이력이 전혀 없는 그룹**뿐이고,
  진입점은 수동과 같은 `create_analysis_job` 하나라 `allow_ai_analysis`·멱등·일일
  한도가 그대로 상한이 된다. 임계치 기반 자동 실행·자동 재분석은 추가하지 않는다.
- `/api/**` 는 **전부 인증이 필요하다**(로그인·로그아웃·`/health` 제외). 라우터마다
  의존성을 붙이지 않고 앱 전역 의존성 한 곳에서 강제한다 — 기본값이 "닫힘"이어야
  새 라우터가 조용히 열리지 않는다. `viewer` 는 GET 만 할 수 있다.
- `estimated_cost` 는 추정이다. 화면에서 "추정" 표기를 유지한다.
  단가표에 모델이 없으면 값은 **`null` 이며 0 이 아니다** — 0 으로 적으면 "쌌다"로 읽힌다.
  항목 전부가 `null` 이면 합계(`total_estimated_cost`)도 `null` 이고, 화면은 `-` 로 쓴다.

### 한도의 기준 두 가지 (자주 어긋나는 지점)

- **일일 분석 한도는 `app_settings.timezone` 의 로컬 자정 기준이다** (기본
  `Asia/Seoul`). 전역(`app_settings.daily_analysis_limit`)과 정책별
  (`analysis_policies.daily_analysis_limit`) 모두 `analysis_jobs.requested_at` 이
  "그 타임존의 오늘 00:00 이후"인 건수를 센다. 기준은 **서버 로케일이나 컨테이너 TZ 가
  아니라 이 설정값** 이다 — 서버가 UTC 로 돌아도 기본값이면 KST 자정에 리셋된다.
  `PUT /api/settings/timezone` 으로 바꾼다 (IANA 이름만 받는다; 오타는 422).
  429 응답 `detail` 에 기준 타임존과 "자정에 리셋" 문구가 실린다.
- **정책의 `default_range_minutes` 는 기본값이자 실행 상한이다.** 이름은 "기본"이지만
  `POST /policies/{id}/query-runs` 가 기간을 명시해도 이 값(과 서버 상한
  `AILA_MAX_QUERY_RANGE_MINUTES` 중 작은 쪽)으로 **clamp** 한다. 422 로 튕기지 않고
  조용히 줄이지도 않는다 — 조정 사실은 `query_runs.warnings` 에 `range_clamped` 로 남는다.
  더 넓은 기간을 조회하려면 정책의 값을 올려야 한다.

---

## 인증 (Phase 5)

`/api/**` 는 세션 쿠키 인증이 필요하다. 예외는 로그인·로그아웃과 `/api` 밖의 경로
(`/health`, `/docs`, `/openapi.json`)뿐이다. **문서(`/docs`)는 로컬 편의상 열어 둔다.**

| 라우트 | 계약 |
| --- | --- |
| `POST /api/auth/login` | `{username, password}` → `200 {username, role}` + httpOnly 세션 쿠키 / 실패 `401` |
| `POST /api/auth/logout` | `204` — **서버의 세션 행을 지운다**(쿠키 삭제만이 아니다) |
| `GET /api/auth/me` | `200 {username, role}` / 미인증 `401` |
| `POST /api/auth/users` | `{username, password, role}` → `201 {username, role}` (admin 전용) |

- `role` 은 `admin` | `viewer` 다. **viewer 는 GET 만** 할 수 있고 그 외 메서드는
  `403 {detail}` 이다. 미인증은 `401 {detail}` 이며, 프런트는 401 을 가로채 `/login`
  으로 보낸다.
- 쿠키는 `httpOnly` + `SameSite=Lax` 다. JS 가 읽을 수 없고, 타 사이트에서 시작된
  POST 에 쿠키가 실리지 않는다. HTTPS 뒤에 둘 때는 `AILA_SESSION_COOKIE_SECURE=true`.
- 세션 수명은 기본 12 시간(`AILA_SESSION_TTL_HOURS`). 만료된 세션은 요청 시점에
  거절되고 행이 지워진다.
- 비밀번호는 stdlib `hashlib.scrypt` 로 salt 와 함께 해시한다(새 의존성 없음).
  저장 형식은 `scrypt$n$r$p$salt$hash` 로 파라미터를 값 안에 담아, 나중에 비용을
  올려도 기존 계정이 그대로 로그인된다.
- 쿠키 토큰의 **SHA-256 해시만** DB(`user_sessions`)에 저장한다 — 테이블을 읽어도
  유효한 쿠키를 만들 수 없다.

> ⚠️ **기본 관리자 계정은 `admin` / `admin`** (`AILA_ADMIN_USERNAME` /
> `AILA_ADMIN_PASSWORD`). 앱 기동 시 해당 계정이 없으면 만들어진다. 이미 있으면
> **아무것도 하지 않는다** — 재기동마다 env 값으로 되돌리면 운영자가 바꾼 비밀번호가
> 매번 사라지기 때문이다. 로컬 밖에 노출한다면 반드시 교체한다.

viewer 계정 만들기:

```powershell
curl.exe -c admin.jar -X POST http://localhost:8000/api/auth/login `
  -H "Content-Type: application/json" -d '{\"username\":\"admin\",\"password\":\"admin\"}'
curl.exe -b admin.jar -X POST http://localhost:8000/api/auth/users `
  -H "Content-Type: application/json" -d '{\"username\":\"watcher\",\"password\":\"...\",\"role\":\"viewer\"}'
```

---

## 스케줄러 (Phase 5)

정책에 세 필드가 붙었다 (`analysis_policies`, revision 0004).

| 필드 | 뜻 |
| --- | --- |
| `schedule_enabled` | 주기 실행 켜기. 켜면 `schedule_interval_minutes` 가 **필수**(없으면 422) |
| `schedule_interval_minutes` | 실행 주기(분) |
| `auto_analyze_new` | 실행 직후 **신규 fingerprint 만** 자동 분석 |

- 백엔드 lifespan 이 60 초 주기(`AILA_SCHEDULER_TICK_SECONDS`)로 tick 을 돈다.
  due 판정은 **마지막 `query_run` 의 `started_at` 이후 interval 경과** 다 —
  수동 실행도 같은 테이블에 남으므로, 방금 손으로 돌린 정책은 한 주기 미뤄진다.
  실패한 회차도 "돌았다"로 친다(`finished_at` 이 아니라 `started_at` 기준인 이유).
- 스케줄이 만든 행은 `query_runs.triggered_by` / `analysis_jobs.triggered_by` 가
  `"schedule"` 이다(수동은 `"manual"`). 이력·화면 배지용이며 **동작을 분기하지 않는다** —
  분기가 생기는 순간 "스케줄 실행만 한도를 안 탄다" 같은 구멍이 열린다.
- `auto_analyze_new` 의 대상은 그 회차 그룹 중 **fingerprint 분석 이력이 전혀 없는
  것**뿐이다. 실패한 분석도 "이력 있음"으로 친다 — 실패를 미분석으로 세면 같은 실패를
  매 회차 다시 태운다. 진입점은 수동과 같은 `create_analysis_job` 이라
  `allow_ai_analysis`·멱등·일일 한도(429)가 그대로 비용 상한이다. 한도 초과·LLM
  연결 부재는 예외 없이 **스킵**하고 tick 로그로만 남긴다.
- **단일 uvicorn 워커 전제다.** 겹침 방지가 in-process 락이라, 워커를 늘리면 같은
  정책이 동시에 두 번 돌 수 있다(겉으로는 정상 동작처럼 보인다). 끄려면
  `AILA_SCHEDULER_ENABLED=false`.

---

## 통합 대시보드 (Phase 5)

`GET /api/dashboard/summary` 는 **정책 전체**를 한 줄씩 준다 (첫 화면).
정책 하나의 추이·상위 그룹은 기존 `GET /api/dashboard/overview` 가 그대로 담당한다.

```jsonc
{
  "generated_at": "...",
  "policies": [{
    "policy_id": 1, "name": "...", "active": true,
    "schedule_enabled": true, "schedule_interval_minutes": 30,
    "last_run": {"id": 12, "started_at": "...", "status": "succeeded",
                 "fetched_count": 263, "group_count": 3, "warnings": [...]},
    "unanalyzed_group_count": 2,   // 최근 성공 run 의 그룹 중 분석 이력이 없는 수
    "total_errors_24h": 1596.0,    // count_over_time 최근 24h. 실패하면 null
    "warnings": [{"code": "count_query_failed", "message": "..."}]
  }]
}
```

- `total_errors_24h` 는 실패 시 **0 이 아니라 `null`** 이다 (0 은 "오류가 없었다"로
  읽힌다). 사유는 `warnings` 의 코드로 남는다 — `policy_inactive`,
  `connection_unavailable`, `count_unsupported`, `count_query_failed`,
  `count_query_timeout`.
- 정책 수만큼 Loki 호출이 나가므로 **정책별 타임아웃 + 실패 격리**가 걸려 있다.
  한 정책의 Loki 가 죽어도 나머지 줄은 그대로 나온다.
- `unanalyzed_group_count` 는 **최근 성공 회차**의 그룹을 fingerprint 로 LEFT JOIN 해
  센다(그룹 id 기준이 아니다 — id 는 회차마다 새로 생긴다). 성공 회차가 없으면 0 이고
  `no_successful_run` 경고가 붙는다.
- 비활성 정책도 목록에서 빼지 않는다 — 빼면 "지웠나?"와 구분되지 않는다.

---

## Phase 6 — 계정 관리 · 검색 · 사용량 분해 · 전체 오류 그룹

2 차 사용 피드백의 API 확장 5 건. **전부 additive** 다 — 기존 응답에서 사라진 필드도,
뜻이 바뀐 필드도 없다. 새 파라미터를 주지 않으면 응답은 Phase 5 와 동일하다.

### 계정 관리 (admin 전용)

| 라우트 | 계약 |
| --- | --- |
| `GET /api/auth/users` | `{total, items:[{id, username, role, active, created_at}]}` |
| `PATCH /api/auth/users/{id}` | `{role?, active?, password?}` → 갱신된 계정 한 줄 |
| `DELETE /api/auth/users/{id}` | **비활성화**(`active=false`) — 실삭제가 아니다 |

- **읽기도 admin 전용이다.** viewer 는 GET 이 열려 있지만 계정 목록만은 예외로 403 이다
  (사용자 이름 목록은 계정 열거의 절반이다).
- **마지막 남은 활성 admin 은 강등도 비활성도 안 된다 (409).** 비활성 admin 은 "관리자가
  또 있다"의 근거가 되지 못한다 — 로그인 자체가 막힌 계정이기 때문이다.
- **자기 자신은 비활성화할 수 없다 (409).** `DELETE` 와 `PATCH {active:false}` 둘 다.
- **`active=false` 와 비밀번호 변경은 그 계정의 세션을 전부 무효화한다** (`user_sessions`
  행 삭제). 자기 비밀번호를 바꾸면 자기 세션도 끊긴다 — 유출을 전제로 하는 조작이라 의도다.
- 비밀번호는 기존 형식 그대로 저장된다 (`scrypt$n$r$p$salt$hash`).
- 응답 본문은 `UserRead`(username·role) 의 **상위 집합**이다. 해시는 어떤 경로로도 나가지 않는다.

### 분석 이력 검색 — `GET /api/analysis-jobs`

추가 파라미터 세 개. 기존 `status`·`limit`·`offset` 과 응답 봉투는 그대로다.

| 파라미터 | 뜻 |
| --- | --- |
| `q` | 부분 일치 — 서비스·정규화 메시지(`error_groups`) **또는** 모델·fingerprint(`analysis_jobs`) |
| `requested_from` / `requested_to` | `requested_at` 범위 (ISO datetime, 경계 포함) |

- `q` 는 대소문자를 가리지 않고, `%`·`_` 는 **리터럴**로 취급한다(이스케이프). 검색어
  하나가 전체 일치가 되는 사고를 막는다.
- 날짜는 **UTC 로 정규화해** 비교한다. 오프셋이 붙은 값(`...+09:00`)은 그 오프셋대로,
  오프셋이 없는 값은 UTC 로 읽는다.
- `total` 도 같은 조건으로 센다 — items 에만 필터를 걸면 있지도 않은 페이지가 생긴다.

### 사용량 분해 — `GET /api/usage?group_by=day|policy`

`group_by` 를 주면 같은 기간·필터를 한 번 더 분해해 `buckets` 를 **추가**한다
(`[{key, label, input_tokens, output_tokens, estimated_cost, job_count, failure_count}]`).
기존 모델별 `items` 는 그대로다.

- `day` 의 하루 경계는 **`app_settings.timezone` 의 로컬 자정**이다 — 일일 분석 한도와 같은
  규칙이라야 "오늘 3 건 썼다"가 두 화면에서 같은 뜻이 된다. `key`=`label`=`YYYY-MM-DD`.
- `policy` 는 usage → job → group → run → policy 조인이다. `key`=정책 id 문자열,
  `label`=정책명. 고리가 끊긴 기록(정책이 실제로 삭제된 경우)은 `key="unknown"` 으로
  모이고 목록 끝에 온다 — 빼 버리면 버킷 합과 `total_jobs` 가 어긋난다.
- 버킷의 `estimated_cost` 는 계산 가능한 기록이 하나도 없으면 **`null`** 이다 (0 금지).
- `group_by` 를 주지 않으면 `buckets` 는 `null` 이다 — "분해를 요청하지 않았다"와
  "분해했더니 비었다"를 구분한다. 모르는 값은 422 다.

### 전체 오류 그룹 — `GET /api/dashboard/error-groups?limit=&offset=`

**전 활성 정책의 최신 성공 조회 회차**의 그룹을 한데 모아 `count desc, last_seen desc`
로 준다. 항목은 기존 그룹 요약 + `policy_id`·`policy_name` 이고, 봉투는
`{total, limit, offset, items}` 다.

- *활성*·*최신*·*성공* 세 조건에 각각 이유가 있다. 비활성 정책을 섞으면 이미 끄기로 한
  오류가 상위를 차지하고, 회차를 안 좁히면 같은 오류가 회차 수만큼 중복되며, 실패 회차를
  최신으로 잡으면 목록이 조용히 빈다.
- 분석 상태·severity 는 기존 그룹 목록과 **같은 fingerprint 조인**으로 붙는다.
- 서로 겹치는 정책(예: 서비스별 정책 + 전체 정책)이 같은 오류를 잡으면 **같은
  fingerprint 가 정책 수만큼 나온다.** 중복이 아니라 "두 정책이 같은 오류를 보고 있다"는
  사실이고, `policy_name` 으로 구분된다.

### summary 카드 시계열 — `GET /api/dashboard/summary`

정책 항목마다 `series_24h: [{timestamp, value}]` 가 추가됐다.

- **`total_errors_24h` 를 만든 바로 그 `count_over_time` 응답**의 포인트다 (step 3600).
  Loki 호출 수는 Phase 5 와 같다 — 시계열 때문에 두 번 부르지 않는다.
- metric 은 `sum by (service)` 라 한 시각에 점이 여러 개 온다. 카드 차트가 같은 시각을
  여러 번 그리지 않도록 **시각 기준으로 합쳐서** 싣는다 (그래서 시리즈 합 = `total_errors_24h`).
- 건수 조회가 실패하면 `total_errors_24h` 는 `null`, `series_24h` 는 **빈 배열**이다
  (0 짜리 선을 그리면 "오류가 없었다"로 읽힌다).

## Phase 7 — 대시보드 지표 확장

지표 공백 검증(옵시디언 "로그 모니터링 지표와 현재 대시보드의 공백" 문서)의 후속.
접기·COUNT 공용 규칙은 `backend/app/dashboard/counting.py` 한 곳에 있다.

### overview 확장 — `GET /api/dashboard/overview`

- **`series` 는 시각별 합산이다** (summary 와 같은 규칙 — 같은 timestamp 는 한 번만
  온다). 서비스별 분해는 `by_service` 가 담당한다. Phase 6 까지는 overview 만 접지
  않아 서비스가 여럿이면 차트가 같은 시각을 여러 번 그렸다 — 이것이 이 phase 의
  출발점이 된 결함이다.
- **`group_count`·`unanalyzed_group_count`**: 조회 회차 **전체**의 DB COUNT.
  `top_groups`(상위 N) 길이와 다르며, 회차가 없으면 0 이 아니라 `null` 이다.
- **`ingest_total`·`ingest_series`·`error_ratio`**: 정책의 `baseline_query`(분모
  쿼리 — 오류 셀렉터와 같은 라벨 범위의 **전체** 로그를 세는 쿼리)가 있을 때만
  같은 provider·기간·step 으로 metric 1 회를 더 걸어 계산한다. 분모를 오류 쿼리에서
  역산하지 않는다 — 미설정·실패는 `null` 이고(0 금지), 실패 사유는
  `baseline_query_failed` 경고 하나로만 나간다. summary(홈)에는 넣지 않는다
  (정책당 Loki 호출 2 배 방지).

### 수집 중단 경고 — 연결의 `expected_services`

연결에 기대 서비스 목록(표준 필드 `service` 기준)을 적어 두면, **조회 실행마다**
(수동·스케줄 동일 경로 `_execute_query_run`) 그 기간에 로그를 한 줄도 내지 않은
서비스를 `ingest_absent` 경고로 `query_runs.warnings` 에 남긴다.

- **경고 기록뿐이다** — 알림·자동 실행 없음. "자동 트리거는 `auto_analyze_new`
  하나" 계약은 그대로다.
- 확인 쿼리는 정책 쿼리가 아니라 **라벨 셀렉터**다. 정책은 보통 `level="ERROR"` 로
  좁혀져 있어, 그 쿼리로는 "오류 없는 정상 서비스"와 "로그가 끊긴 서비스"가 똑같이
  0 으로 보인다.
- 확인 자체의 실패는 `presence_check_failed` 로 강등하고 조회는 성공으로 남긴다.
  `supports_presence=False` 어댑터(capability 플래그)는 경고 없이 건너뛴다.
- 화면: 정책 상세와 홈 카드에 "수집 중단 의심" 배지. 연결 관리는 신설된
  `/admin/log-source-connections` 에서 한다.

### 일일 한도 게이지 — `GET /api/usage/daily-limit`

`{date, timezone, global_limit, global_used, policies[]}`. 사용량·하루 경계는
429 를 내는 한도 검사와 **같은 함수**(`analysis.service.daily_usage`, usage 쪽은
`usage/integrations.py` 지연 import)다 — 게이지와 429 가 다른 숫자를 보이면
게이지는 없느니만 못하다. `policies` 는 **자체 `daily_analysis_limit` 이 설정된
정책만** 싣는다. 엔드포인트가 없는 옛 백엔드에서 프론트는 게이지를 감춘다
(`0/0` 은 "다 썼다"로 읽힌다).
