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

## 디렉터리 구조

```
aila/
├── README.md
├── .gitignore
├── docker-compose.yml            # Phase 1 인프라 트랙이 만든다 (아직 없음)
├── infra/                        # Loki·Alloy·Grafana·demo-api 설정, 장애 시나리오
│   └── README.md
├── frontend/                     # React + TypeScript + Vite
│   └── README.md
└── backend/
    ├── pyproject.toml            # [freeze] 의존성 선언
    ├── alembic.ini               # ASCII only (Windows cp949 이슈)
    ├── .env.example
    ├── alembic/
    │   ├── env.py
    │   └── versions/0001_initial_schema.py   # [freeze] models.py 와 1:1
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
