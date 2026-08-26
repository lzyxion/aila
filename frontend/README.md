# frontend/

**소유: Phase 1 — 프론트엔드 트랙**

AILA 웹 UI. React + TypeScript + Vite / Tailwind CSS v4 / TanStack Query / Recharts /
react-router. UI 언어는 한국어다.

## 실행

```powershell
cd frontend
npm install
Copy-Item .env.example .env.local     # 처음 한 번
npm run dev        # http://localhost:5173
npm run build      # tsc --noEmit && vite build
npm run typecheck  # tsc --noEmit 만
```

`.env.local` 의 `VITE_USE_MOCK` 이 기본 스위치다.

| 값 | 동작 |
| --- | --- |
| `true` (기본) | 네트워크를 타지 않고 `src/api/mock/` 의 fixture 로 응답한다. 백엔드 없이 5 개 화면을 끝까지 확인할 수 있다. |
| `false` | 실제 백엔드에 붙는다. dev 서버가 `/api` 를 `http://localhost:8000` 으로 프록시한다 (`AILA_BACKEND_ORIGIN` 으로 변경 가능). |

Phase 1 시점에는 백엔드 대부분의 엔드포인트가 501 이라 기본값을 `true` 로 두었다.
`false` 로 두고 붙으면 각 화면이 501 을 오류가 아니라 "아직 구현되지 않음" 안내로 표시한다.

## 화면

| 경로 | 화면 | 핵심 요소 |
| --- | --- | --- |
| `/` | 대시보드 | 기간 선택, **정책 선택 + 정책 실행(강조 버튼)을 한 묶음으로**, 실행 중·완료 피드백, 시간대별 오류 건수(Area), 서비스별 건수(Bar), 상위 오류 그룹 표 |
| `/policies` | 분석 정책 관리 | 목록(활성·비활성 모두, 배지 표시) + 생성/수정 폼(**활성 토글**), 연결 테스트, **저장 전 미리보기**, 행 선택 시 **상세 + 실행 이력** |
| `/query-runs/:runId` | 조회 회차 | 그 회차의 조회 라인·제외·경고와 **오류 그룹 목록** (정책 실행 이력에서 진입) |
| `/llm-connections` | LLM 연결 관리 | 프로바이더별 **모델 드롭다운**(+직접 입력 폴백), base URL 은 `openai_compatible` 일 때만, API 키(한 줄 고정·마스킹 표시), 연결 테스트, 기본 연결 지정 |
| `/error-groups/:groupId` | 오류 그룹 상세 | 요약·추이·라벨, 마스킹된 대표 로그, AI 분석 실행·폴링, 결과, 보고서 내보내기 |
| `/usage` | 분석 이력·사용량 | 실행 목록·상태, 모델별 토큰·추정 비용·평균 응답 시간, **단가 미등록 모델의 인라인 단가 등록** |

## 구조

```
src/
├── api/
│   ├── types.ts          # backend/app/schemas/*.py 를 손으로 옮긴 타입 (계약)
│   ├── client.ts         # fetch 래퍼 · ApiError · mock 분기
│   ├── endpoints.ts      # 라우트별 타입 지정 래퍼
│   ├── queries.ts        # TanStack Query 훅 · 캐시 키
│   └── mock/             # fixture 와 mock 라우터 (VITE_USE_MOCK=true 일 때만 로드)
├── components/           # ui 원시 요소 · 레이아웃 · 상태 배지 · 차트
├── lib/                  # 표기 헬퍼(format) · Markdown 보고서 렌더러(report)
└── pages/                # 화면 6 개 + 404
```

`src/api/types.ts` 가 백엔드 계약의 사본이다. 백엔드 스키마가 바뀌면 **이 파일을** 고친다.
백엔드 `schemas/**` 는 동결이라 프론트에서 건드리지 않는다.

## 표시 규칙 (계약상 제약)

코드 주석과 UI 문구 양쪽에 박아 두었다. 리팩터링할 때 같이 지운다면 계약을 깨는 것이다.

- 화면에 나오는 로그 라인은 전부 **마스킹된 값**이다. mock fixture 도 마스킹된 형태로만 쓴다.
- LLM 결과는 사실이 아니라 **원인 가설**로 표시하고, 원본 로그로 돌아갈 경로(라벨 기반 LogQL)를 함께 준다.
- `severity` 는 항상 "LLM 추정"을 붙여 표시하고 발생량 지표와 분리한다.
- `confidence` 는 "가설 순위 힌트"로 적고 확률로 표기하지 않는다.
- `estimated_cost` 는 "추정" 표기를 유지한다.
- 건수·추이는 `count_over_time` metric 기준이며 로그 라인 수가 아니라고 캡션에 적는다.
- 분석 상태는 그룹 id 가 아니라 **fingerprint 기준**이다.
- 기간·라인 수 상한은 서버가 강제한다. UI 입력은 편의일 뿐 신뢰 대상이 아니다.
- 분석은 수동 트리거만 있다. 자동 실행 경로를 추가하지 않는다.
- **분석 작업 폴링이 끝나면 파생 화면을 무효화한다** (`useAnalysisJobWithRefresh`).
  "분석 이력"은 작업 단건이 아니라 그룹 상세의 `analyses` 에서 오므로, 작업만 갱신하면
  결과는 보이는데 이력 배지만 `분석 중` 스피너로 남는다.

## 폴백 규칙 (백엔드가 아직 없는 경로)

화면은 "백엔드에 그 경로가 없다"를 **실패로 표시하지 않는다.** `isEndpointMissing`
(`src/api/client.ts`, 404·405·422·501)이 참이면 각 화면이 다음으로 물러난다.

| 경로 | 없을 때 |
| --- | --- |
| `POST /api/llm-connections/models` | 모델 **자유 입력**으로 전환하고 사유를 hint 에 적는다. 드롭다운에는 항상 "직접 입력" 항목이 있다. |
| `GET /api/policies/{id}/query-runs` | 실행 이력 자리에 안내 문구(대시보드 실행 직후 링크로 회차를 볼 수 있다). |
| `GET /api/analysis-jobs` | `/usage` 의 실행 목록이 "엔드포인트 없음" 안내를 표시한다. |

> 모델 목록은 **조회지만 POST 다.** `api_key` 를 쿼리스트링에 실으면 평문 키가 서버
> 액세스 로그·프록시 로그·브라우저 히스토리에 남는다. 그 경로가 없는 백엔드는 405
> (같은 경로의 다른 메서드) 나 404 를 주고, 둘 다 폴백에 잡힌다.
>
> 405·422 를 폴백에 넣은 이유: 같은 prefix 의 기존 라우트가 요청을 먼저 잡아 "메서드
> 없음"이나 "경로 파라미터 파싱 실패"로 응답하는 경우가 있다. 이 문구를 사용자에게
> 그대로 보여주면 아무 도움이 되지 않는다.

## 모델 단가 (`/usage`)

프로바이더 API 는 단가를 제공하지 않는다. 그래서 단가 미등록 모델 행에는 `-` 옆에
**단가 등록** 인라인 폼이 붙어 있고, `PUT /api/settings/model_pricing` 으로 저장한다.
단위는 **1K 토큰당**이며(`app/analysis/pricing.py` 와 동일), `PUT` 이 키를 통째로
교체하므로 훅(`useUpsertModelPricing`)이 **기존 표를 읽어 병합**한 뒤 보낸다.
등록해도 **이미 기록된 실행은 소급 계산하지 않는다** — 화면 문구도 그렇게 적혀 있다.

## 차트

색은 역할로 고른다 — 단일 시리즈(오류 추이, 서비스별 건수)는 한 색만 쓰고 범례를 두지 않으며,
두 시리즈인 토큰 차트만 categorical 2 색 + 범례를 쓴다. 두 색(`#2a78d6`, `#eb6834`)은
흰 배경 기준 CVD ΔE 24.7 / 일반 시야 ΔE 33.6 / 대비 3:1 이상으로 검증했다.
Recharts 는 초기 번들에서 떼어 `src/components/chartsLazy.tsx` 로 지연 로딩한다.
