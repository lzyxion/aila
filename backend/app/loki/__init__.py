"""Loki 어댑터 — `LogSourceProvider` 의 첫 구현체.

Phase 1 담당 트랙: **Loki 어댑터** (`app/loki` + `app/connections`)

여기서 구현할 것:
- `LokiProvider(LogSourceProvider)` — httpx 로 Loki HTTP API 호출
  - `test_connection()`  : `GET /ready` 또는 `GET /loki/api/v1/labels`
  - `list_labels()`      : `GET /loki/api/v1/labels`, `/label/{name}/values`
  - `fetch_logs()`       : `GET /loki/api/v1/query_range` (로그 쿼리)
  - `count_over_time()`  : `GET /loki/api/v1/query_range` (metric 쿼리)
- Loki 응답 -> `LogRecord` 정규화. 표준 필드(service/environment/level)는
  연결의 `label_mapping` 을 거쳐 채운다.

**소스 고유 지식은 이 패키지 밖으로 새어 나가면 안 된다:**
- `| json` 파서 스테이지는 파싱 실패 줄에 `__error__` 라벨을 붙여 통과시킨다.
  후속 필터가 그 줄을 지우므로 비정형 오류가 조용히 사라진다 →
  파싱 실패 건수를 세어 `FetchResult.dropped` / `warnings` 로 올린다.
- Loki 기본 반환 한도 5,000 줄 → 걸리면 `truncated=True`.
- `tail`(스트리밍)은 MVP 에서 쓰지 않는다. `query_range` 만 쓴다.
- Loki 는 쓰기 대상이 아니다. **읽기 전용.**
"""
