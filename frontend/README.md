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
| `/` | 대시보드 | 기간·정책 선택, 정책 실행, 시간대별 오류 건수(Area), 서비스별 건수(Bar), 상위 오류 그룹 표 |
| `/policies` | 분석 정책 관리 | 목록 + 생성/수정 폼, 연결 선택·연결 테스트, **저장 전 미리보기** |
| `/llm-connections` | LLM 연결 관리 | 프로바이더·모델·API 키(마스킹 표시)·base URL, 연결 테스트, 기본 연결 지정 |
| `/error-groups/:groupId` | 오류 그룹 상세 | 요약·추이·라벨, 마스킹된 대표 로그, AI 분석 실행·폴링, 결과, 보고서 내보내기 |
| `/usage` | 분석 이력·사용량 | 실행 목록·상태, 모델별 토큰·추정 비용·평균 응답 시간 |

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
└── pages/                # 화면 5 개 + 404
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

## 알려진 계약 공백

- `GET /api/analysis-jobs` (분석 실행 **목록**) 이 백엔드 API 초안에 없다. 설계 문서
  "분석 이력·사용량" 화면이 요구하므로 mock 에는 넣어 두었고, 라이브 모드에서는
  `/usage` 화면이 404/501 을 "엔드포인트 없음" 안내로 표시한다. 백엔드에 추가되면
  `src/api/endpoints.ts` 의 주석을 지우면 된다.

## 차트

색은 역할로 고른다 — 단일 시리즈(오류 추이, 서비스별 건수)는 한 색만 쓰고 범례를 두지 않으며,
두 시리즈인 토큰 차트만 categorical 2 색 + 범례를 쓴다. 두 색(`#2a78d6`, `#eb6834`)은
흰 배경 기준 CVD ΔE 24.7 / 일반 시야 ΔE 33.6 / 대비 3:1 이상으로 검증했다.
Recharts 는 초기 번들에서 떼어 `src/components/chartsLazy.tsx` 로 지연 로딩한다.
