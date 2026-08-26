# infra/

**소유: Phase 1 — 인프라 트랙**

이 디렉터리와 저장소 루트의 `docker-compose.yml` 은 인프라 트랙이 단독 소유한다.

수집 경로(demo-api → Alloy → Loki)와 분석 경로(웹 → FastAPI → Loki·DB·LLM)는
**Loki 에서만 만난다.** AILA 백엔드는 Loki 를 읽기만 하고, 수집 파이프라인을 건드리지 않는다.

```
demo-api (stdout JSON) ──> alloy ──push──> loki <──read── backend ──> postgres
                                             ^              │
                                          grafana           └──> llm-mock
                                  (유입 육안 검증 전용)   (LLM 스텁 + 페이로드 감사)
```

---

## 구성

| 서비스 | 이미지 | 포트 | 역할 |
| --- | --- | --- | --- |
| `postgres` | `postgres:16` | 5432 | 백엔드 메타데이터 (정책·분석 작업·사용량) |
| `loki` | `grafana/loki:3.5.0` | 3100 | 단일 바이너리 + filesystem 저장 |
| `alloy` | `grafana/alloy:v1.10.0` | 12345 | demo-api stdout 수집 → Loki push |
| `grafana` | `grafana/grafana:12.0.2` | 3000 | Loki 유입 검증 (익명 Admin) |
| `demo-api` | 로컬 빌드 | 8081 → 8000 | 장애 시나리오 6 종 로그 생산 |
| `llm-mock` | 로컬 빌드 | 8090 → 8000 | OpenAI 호환 LLM 스텁 + 전송 페이로드 감사 |
| `backend` | 로컬 빌드 | 8000 | **profile `app`** |
| `frontend` | 로컬 빌드 | 5173 | **profile `app`** |

기본 `docker compose up` 은 앞의 6 개를 띄운다. 빌드가 필요한 것은 `demo-api` 와
`llm-mock` 둘뿐이라 **backend/frontend 소스가 없어도 데모 환경 전체가 뜬다.**

### 파일

```
docker-compose.yml               # (저장소 루트)
infra/
├── README.md
├── backend.Dockerfile           # profile "app" 전용. 컨텍스트는 ../backend
├── frontend.Dockerfile          # profile "app" 전용. 컨텍스트는 ../frontend
├── loki/loki-config.yaml
├── alloy/config.alloy
├── grafana/
│   ├── provisioning/datasources/loki.yaml
│   ├── provisioning/dashboards/dashboards.yaml
│   └── dashboards/aila-ingest.json
├── demo-api/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/{__init__,logging_setup,scenarios,main}.py
├── llm-mock/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/{__init__,generator,main}.py
└── scenarios/                   # 시나리오별 expected-analysis.md + 샘플 로그
```

`backend.Dockerfile` / `frontend.Dockerfile` 이 `infra/` 에 있는 것은 실수가 아니다.
인프라 트랙이 `backend/`·`frontend/` 안에 파일을 만들지 않기 위한 배치다
(Phase 1 파일 소유권 규칙). 빌드 컨텍스트만 각 디렉터리를 가리킨다.

---

## 기동

```powershell
cd aila

docker compose up -d --build          # 데모 스택 (postgres·loki·alloy·grafana·demo-api)
docker compose ps
docker compose logs -f demo-api

docker compose down                   # 정지 (볼륨 유지)
docker compose down -v                # 정지 + 로그·DB 데이터까지 삭제
```

`app` 프로필까지:

```powershell
docker compose --profile app up -d --build
docker compose --profile app exec backend alembic upgrade head   # 마이그레이션은 수동

# 전 경로 E2E (연결 등록 → 시나리오 → 정책 → 마스킹 → 분석 → 보고서)
..\backend\.venv\Scripts\python.exe ..\scripts\e2e_demo.py
```

> 마이그레이션을 컨테이너 기동 시 자동 실행하지 않는다. 스키마 변경 시점은 사람이 통제해야 한다.

### 환경변수 (전부 기본값이 있다)

저장소 루트에 `.env` 를 두면 compose 가 읽는다. 없어도 동작한다.

| 변수 | 기본값 | 비고 |
| --- | --- | --- |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` | `aila` | |
| `POSTGRES_PORT` | `5432` | 호스트에서 alembic 을 돌리려면 열려 있어야 한다 |
| `LOKI_PORT` / `GRAFANA_PORT` / `DEMO_API_PORT` | `3100` / `3000` / `8081` | |
| `LOKI_IMAGE` / `ALLOY_IMAGE` / `GRAFANA_IMAGE` | 위 표 참조 | 태그가 사라지면 여기서 올린다 |
| `DEMO_SERVICE_NAME` / `DEMO_ENVIRONMENT` | `payment-api` / `staging` | 컨테이너 라벨과 로그 필드 |
| `DEMO_RELEASE` / `DEMO_NEXT_RELEASE` | `v1.4.2` / `v1.5.0` | 시나리오 05 |
| `DEMO_AUTO_TRAFFIC` | `true` | 백그라운드 트래픽 루프 |
| `DEMO_INTERVAL_SECONDS` / `DEMO_EVENTS_PER_TICK` | `5` / `3` | 유입량 조절 |
| `LLM_MOCK_PORT` / `LLM_MOCK_MODEL` | `8090` / `llm-mock-1` | llm-mock |
| `LLM_MOCK_LATENCY_MS` / `LLM_MOCK_HISTORY` | `0` / `50` | 응답 지연·요청 보관 수 |
| `AILA_ENCRYPTION_KEY` | 저장소에 적힌 데모 키 | profile `app` 의 backend 용 Fernet 키 |
| `VITE_USE_MOCK` | `false` | 프런트 라이브 모드 (true 면 fixture 만 본다) |
| `AILA_BACKEND_ORIGIN` | `http://backend:8000` | 프런트 dev 서버의 `/api` 프록시 대상 |

`AILA_ENCRYPTION_KEY` 는 **기본값이 compose 에 그대로 적혀 있다.** 이게 없으면
`compose up` 만으로 연결 등록이 실패해서 "Compose 만으로 데모를 재현한다"는 기준이
깨지기 때문이다. 로컬·데모 전용이라 감수하는 선택이고, 데모 밖으로 나가는 순간 바꾼다.
바꾸면 기존에 저장된 secret 은 복호화되지 않으므로 연결을 다시 등록해야 한다.

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 저장소 루트 .env 에 AILA_ENCRYPTION_KEY=... 로 넣는다 (.env 는 커밋 금지)
```

> `frontend` 컨테이너는 소스를 **이미지에 COPY** 한다 (bind mount 가 아니다).
> 호스트에서 `frontend/src` 를 고쳐도 컨테이너에는 반영되지 않는다 —
> `docker compose --profile app up -d --build frontend` 로 다시 굽거나,
> 호스트에서 `npm run dev` 를 띄운다.

---

## 시나리오 트리거

`demo-api` 는 뜨는 즉시 백그라운드 루프로 트래픽을 만든다 (기본 5 초마다 3 이벤트).
**아무것도 하지 않아도 Loki 에 데이터가 쌓인다** — "결과가 비었다"는 증상이 조회 버그인지
수집 실패인지 구분하려면 이 기준선이 항상 있어야 한다.

시점이 중요한 검증(급증 감지, 배포 전후 비교)에는 엔드포인트를 쓴다.

```powershell
# 시나리오 목록과 현재 release
curl.exe "http://localhost:8081/scenarios"

# 1) 결제 외부 API 타임아웃 — TimeoutError, 504
curl.exe "http://localhost:8081/payment/charge"

# 2) DB 연결 실패 — DatabaseConnectionError, 500
curl.exe "http://localhost:8081/orders"

# 3) 인증 토큰 만료 — JWT expired, 401  (주의: level 은 WARNING 이다)
curl.exe "http://localhost:8081/auth/me"

# 4) Null 참조 — AttributeError + 스택트레이스, 500
curl.exe "http://localhost:8081/profile"

# 5) 배포 직후 오류 증가 — release 라벨이 바뀌고 180 초간 오류율 상승
curl.exe -X POST "http://localhost:8081/admin/deploy?release=v1.5.0&spike_seconds=180"
curl.exe -X POST "http://localhost:8081/admin/deploy"      # 되돌리기 (토글)

# 6) 비밀값 포함 — 가짜 Bearer/이메일/카드번호, 502
curl.exe "http://localhost:8081/debug/dump-context"

# 급증 만들기 (빈도 급증 감지용)
curl.exe -X POST "http://localhost:8081/scenarios/payment-timeout/burst?count=120"

# 비정형(non-JSON) 라인만 내보내기 — `| json` 파싱 실패 케이스
curl.exe -X POST "http://localhost:8081/scenarios/noise?count=20"
```

시나리오별 기대 원인·확인 절차는 `infra/scenarios/<id>/expected-analysis.md` 에 있다.

> 시나리오 06 의 토큰·이메일·카드번호는 **전부 명백한 가짜다**
> (`infra/demo-api/app/scenarios.py` 상단 상수 참조). 실제 자격증명을 넣지 말 것.

---

## llm-mock — LLM 스텁 겸 전송 페이로드 감사 지점

`infra/llm-mock/` 은 **OpenAI 호환 `/v1/chat/completions` 스텁**이다. 외부로 나가는
네트워크 호출이 전혀 없고, 요청은 컨테이너 메모리에만 남는다 (재시작하면 사라진다).

두 가지 이유로 존재한다.

1. **키 없이 데모가 끝까지 돈다.** 분석 단계에 실제 LLM 키가 필요하면
   "Docker Compose 만으로 데모 환경을 재현한다"는 MVP 성공 기준이 깨진다.
2. **보낸 요청을 되돌려 받을 수 있다.** 설계 문서는 시나리오 06 의 검증을
   "LLM 요청 페이로드에 원문 토큰이 없음을 단언하는 자동 테스트"로 못박았다.
   진짜 프로바이더는 우리가 보낸 요청 본문을 돌려주지 않으므로, 그 단언은
   여기서만 가능하다. `scripts/e2e_demo.py` 의 g 단계가 이 엔드포인트를 쓴다.

응답 본문은 하드코딩이 아니라 **요청에 실려 온 `response_format.json_schema` 를 순회해서**
만든다. 백엔드가 `AnalysisResultSchema` 를 바꾸면 스텁도 따라 바뀌고, 스키마를 어기면
백엔드의 공통 검증 경로(`parse_analysis_result`)가 그 자리에서 잡아낸다.

| 엔드포인트 | 용도 |
| --- | --- |
| `POST /v1/chat/completions` | 스키마에 맞는 한국어 분석 결과 + `usage` 토큰. `response_format` 이 없으면(=연결 테스트) `pong` 만 돌려준다 |
| `GET /debug/last-request` | 마지막 요청. `?kind=analysis` 로 연결 테스트(ping)를 걸러낸다 |
| `GET /debug/requests` | 최근 요청 목록 (`?kind=`, `?limit=`) |
| `POST /debug/reset` | 보관 중인 요청 비우기 (E2E 재실행 전) |
| `GET /v1/models`, `GET /health` | 헬스·모델 목록 |

`/debug/*` 응답의 `prompt_text` 는 system·user 메시지를 이어붙인 문자열이다.
마스킹 감사는 여기 하나만 훑으면 된다.

```powershell
curl.exe "http://localhost:8090/health"
curl.exe "http://localhost:8090/debug/last-request?kind=analysis"
```

> **Alloy 수집 대상이 아니다** (`aila.logs` 라벨을 붙이지 않았다).
> 프롬프트가 Loki 로 들어가면 마스킹 감사가 의미를 잃는다.
> 인증도 없으므로 이 컨테이너를 로컬 밖에 노출하지 말 것.

### 실제 LLM(OpenAI·Anthropic)으로 바꾸기

llm-mock 은 **연결 설정 하나**로 갈아끼운다. compose 를 고칠 필요가 없다.
UI 는 `LLM 연결` 화면, API 는 `POST /api/llm-connections` 다.

| provider | model 예시 | base_url | api_key |
| --- | --- | --- | --- |
| `openai` | `gpt-4o-mini` | 비움 (기본 엔드포인트) | `sk-...` |
| `anthropic` | `claude-sonnet-4-6` | 비움 | `sk-ant-...` |
| `openai_compatible` | 게이트웨이가 정한 이름 | **필수** (`http://.../v1`) | 게이트웨이 정책에 따름 |

```powershell
curl.exe -X POST "http://localhost:8000/api/llm-connections" `
  -H "Content-Type: application/json" `
  -d '{\"name\":\"openai\",\"provider\":\"openai\",\"model\":\"gpt-4o-mini\",\"is_default\":true,\"api_key\":\"sk-...\"}'

# 연결 테스트도 실제 과금 호출이다 (서버가 최소 토큰으로 보낸다)
curl.exe -X POST "http://localhost:8000/api/llm-connections/test" `
  -H "Content-Type: application/json" -d '{\"connection_id\":2}'
```

`is_default=true` 로 만들면 기존 기본 연결이 자동으로 해제되고, 이후 분석은 새 연결로
나간다. 이미 실행된 분석 이력의 `provider`/`model` 은 실행 시점 값으로 고정되어 있으므로
바뀌지 않는다.

바꾸고 나면 **감사 경로가 사라진다는 점**을 기억할 것 — 진짜 프로바이더에 보낸 요청
본문은 되돌려 받을 수 없으므로 `scripts/e2e_demo.py` 의 g 단계는 llm-mock 에서만
의미가 있다. 마스킹 회귀 검증은 llm-mock 기본 연결로 돌린다.

API 키는 저장 시 Fernet 으로 암호화되고 응답에는 마스킹된 값만 실린다.
`AILA_ENCRYPTION_KEY` 를 바꾸면 기존 키는 복호화되지 않으니 연결을 다시 등록해야 한다.
추정 비용을 보려면 `app_settings.model_pricing` 에 단가표가 있어야 한다 — 없으면
`estimated_cost` 는 0 이 아니라 `null` 로 남는다 (지어낸 값을 쓰지 않는다).

---

## Grafana 에서 유입 확인

<http://localhost:3000> — 로그인 없이 들어간다 (익명 Admin, 로그인 폼 비활성).
시작 화면이 프로비저닝된 **"AILA — Loki 유입 검증"** 대시보드다
(못 찾으면 좌측 Dashboards → `AILA` 폴더).

이 Grafana 는 제품 UI 가 아니다. 보는 순서는 이렇다.

1. **최근 5 분 유입 라인 수** — 0 이면 수집이 죽은 것이다.
   `docker compose logs alloy`, `docker compose logs demo-api` 순으로 본다.
2. **service / level 별 발생 추이** — `payment-api`, `order-api`, `auth-api` 세 개가
   보여야 정상이다. 컨테이너는 하나지만 JSON 본문의 `service` 가 라벨을 덮어쓴다.
3. **| json 파싱 실패 라인** — **0 이 아닌 것이 정상이다.** 비정형 로그가
   `| json` 을 통과하지 못하는 상황을 일부러 재현하고 있다.
4. **release 별 ERROR 추이** — 시나리오 05 를 트리거하면 계단이 보인다.

Explore 탭에서 직접 쿼리하려면:

```logql
{service="payment-api"} | json | level="ERROR"
{service=~".+"} | json | __error__="JSONParserErr"
sum by (service, level) (count_over_time({service=~".+"}[1m]))
```

### Grafana 없이 확인 (HTTP API)

```powershell
curl.exe "http://localhost:3100/ready"
curl.exe "http://localhost:3100/loki/api/v1/labels"
curl.exe "http://localhost:3100/loki/api/v1/label/service/values"
curl.exe -G "http://localhost:3100/loki/api/v1/query_range" `
  --data-urlencode "query={service=\"payment-api\"} | json | level=\"ERROR\"" `
  --data-urlencode "limit=5"
```

Alloy 자체 UI 는 <http://localhost:12345> 다. 컴포넌트 그래프에서
`loki.source.docker.aila` 가 타깃을 몇 개 잡았는지 볼 수 있다.

---

## 로그 스키마 (백엔드와의 계약)

demo-api 는 **한 줄 = 한 JSON** 으로 stdout 에 쓴다.

```json
{
  "timestamp": "2026-08-26T03:21:50.417737+00:00",
  "service": "payment-api",
  "environment": "staging",
  "level": "ERROR",
  "release": "v1.4.2",
  "request_id": "req-0255abee-13c3-4b8b-a333-04052054bff4",
  "message": "payment authorization failed: gateway timeout after 31117ms ...",
  "exception": "PaymentGatewayTimeout",
  "stacktrace": "Traceback (most recent call last):\n  File ...",
  "http_status": 504,
  "scenario": "payment-timeout"
}
```

- `stacktrace` 는 **개행이 든 문자열 하나**다. 여러 줄로 출력하면 Alloy 가 줄마다
  별개 엔트리로 읽어 그룹화가 무의미해진다.
- `exception` / `stacktrace` 는 오류가 아닐 때 `null` 로 남는다 (필드를 빼지 않는다).
- `scenario` 는 데모 전용 필드다. 실제 로그에는 없다 —
  fingerprint 입력으로 쓰면 안 된다.

### Alloy 가 올리는 Loki 라벨

| 라벨 | 출처 | 비고 |
| --- | --- | --- |
| `service` | JSON 본문 → 없으면 컨테이너 라벨 `aila.service` | |
| `environment` | JSON 본문 → 없으면 컨테이너 라벨 `aila.environment` | |
| `level` | JSON 본문 | **비정형 라인에는 없다** |
| `release` | JSON 본문 | 시나리오 05 |
| `container`, `stream` | docker 메타데이터 | |
| `collector` | `loki.write` external_labels (`alloy`) | |

`request_id` 와 `exception` 은 **라벨이 아니라 structured metadata** 다.
라벨로 올리면 스트림이 요청마다 갈라진다. LogQL 에서는 파서 없이 바로 필터할 수 있다.

```logql
{service="payment-api"} | exception="PaymentGatewayTimeout"
```

### Loki 가 자동으로 붙이는 라벨 — 백엔드 주의

Loki 3.x 는 설정하지 않아도 두 가지를 스스로 붙인다. 확인된 동작은 이렇다.

- `service_name` — **스트림 라벨**. 값은 우리 `service` 와 같다
  (`/loki/api/v1/labels` 에 실제로 나타난다).
- `detected_level` — **structured metadata**. `/labels` 에는 없지만 쿼리 결과의
  라벨 집합에는 섞여 들어오고, 값의 대소문자가 우리 `level` 과 다를 수 있다
  (`info` vs `INFO`).

둘 다 `LogRecord.labels` 에 그대로 들어오므로 **fingerprint 입력에서 제외**해야 한다.
`service_name` 을 남기면 `service` 와 중복되고, `detected_level` 을 남기면
Loki 버전에 따라 fingerprint 가 흔들린다.

또 `| json` 파이프라인은 이미 라벨로 존재하는 이름과 충돌하는 필드를
`service_extracted`, `level_extracted`, `release_extracted`, `environment_extracted`
로 내놓는다. Loki 어댑터가 `label_mapping` 으로 흡수할 지점이다.

---

## 비정형 로그를 일부러 섞는 이유

LogQL 의 파서 스테이지는 파싱에 실패한 줄에 `__error__` 라벨을 붙이고 **통과시킨다.**
`level="ERROR"` 같은 후속 필터가 그 줄을 걸러내므로, **비정형으로 남은 오류가
조회 결과에서 통째로 사라진다.** 이 상황이 데모 환경에서 실제로 재현되지 않으면
백엔드가 파싱 실패 건수를 `dropped` / `warnings` 로 올리는지 검증할 수 없다.

demo-api 가 내는 비정형 라인은 네 가지다.

1. uvicorn 자체 access log (`INFO:     127.0.0.1:... - "GET /health HTTP/1.1" 200 OK`)
2. 레거시 포맷터 (`2026-08-26 03:22:12 WARN  [payment-api] connection pool usage 82% ...`)
3. Java 스택 조각 (`  at com.example.payment.SettlementJob.run(...)`)
4. 로그 드라이버에 잘린 JSON (`{"timestamp": "...", "message": "truncated by log driver buffer at 16k`)

4 번이 특히 중요하다. JSON 으로 시작해 사람 눈에는 정상으로 보이는데 파싱은 실패한다.

---

## 백엔드와의 접점 (계약)

- 백엔드는 `AILA_DATABASE_URL` 로 PostgreSQL 에 붙는다 (`backend/app/config.py`).
  compose 안에서는 `postgresql+psycopg://aila:aila@postgres:5432/aila`,
  호스트에서 돌릴 때는 `@localhost:5432`.
- 백엔드가 등록할 Loki 연결의 `base_url` 은
  컨테이너 안 `http://loki:3100`, 호스트에서 `http://localhost:3100`.
- 백엔드는 Loki 를 **읽기 전용**으로만 사용한다. 수집 경로는 건드리지 않는다.
- 백엔드 컨테이너에는 `AILA_ENCRYPTION_KEY` (Fernet 키) 를 주입해야 한다.
- Loki 의 `max_entries_limit_per_query` 는 5000 으로,
  `AILA_MAX_LINES_PER_QUERY` 기본값과 맞춰 두었다. 한쪽만 올리면 조용히 잘린다.

---

## 알려진 제약

- **익명 Admin Grafana 와 인증 없는 Loki 는 로컬 전용이다.** MVP 배포 대상이
  로컬·데모로 한정되어 있다는 설계 전제에 기대고 있다. 이 스택을 그대로 외부에 노출하지 말 것.
- `backend/`, `frontend/` 에 `.dockerignore` 가 없다. profile `app` 을 빌드하면
  `backend/.venv` 와 `frontend/node_modules` 까지 빌드 컨텍스트로 전송되어 느리다.
  이미지 내용에는 들어가지 않는다. 각 트랙이 `.dockerignore` 를 추가하면 해결된다.
- `frontend.Dockerfile` 은 Vite **개발 서버**를 띄운다. 정적 빌드 + nginx 이미지가 아니다.
  MVP 배포 대상이 로컬·데모로 한정되어 있어 프로덕션 이미지를 따로 두지 않았다.
  `package.json` 이 없으면 빌드가 실패하지만, profile `app` 뒤에 있으므로
  기본 `docker compose up` 에는 영향이 없다.
- Alloy 컨테이너는 `/var/run/docker.sock` 을 읽는다. docker 소켓 접근은
  사실상 호스트 root 권한이다 — 로컬 데모라 감수하는 선택이다.
