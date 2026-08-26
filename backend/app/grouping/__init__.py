"""오류 그룹화 — 파싱, 가변값 제거, fingerprint 생성, 그룹 집계.

Phase 1 담당 트랙: **그룹화·마스킹** (`app/grouping` + `app/masking`)

구현 순서(설계 문서 "오류 그룹화와 마스킹"):
1. JSON / logfmt / 비정형 로그를 구분해 메시지·예외 타입·스택트레이스를 추출한다.
2. UUID, 요청 ID, 숫자, ISO 시간, IP 등 가변값을 플레이스홀더로 치환한다.
3. `service + exception type + 정규화 메시지 + 상위 스택 프레임` 을 해시해 fingerprint 를 만든다.
4. 같은 fingerprint 를 하나의 그룹으로 묶고 건수·시간대별 건수·대표 로그를 집계한다.

계약상 제약:
- 3 단계에서 쓰는 스택 프레임은 **상위 프레임만**이다. 전체를 해시하면 같은 버그가
  호출 경로 차이로 쪼개지고, 메시지만 해시하면 다른 원인이 한 그룹으로 뭉친다.
- 처리 순서는 **마스킹 → 정규화 → fingerprint** 로 고정한다. 순서를 바꾸면 같은 로그의
  fingerprint 가 마스킹 적용 여부에 따라 달라진다.
- 입력은 `app.schemas.logrecord.LogRecord` 뿐이다. Loki 응답 형식을 직접 보지 않는다.
- fingerprint 는 결정적이어야 한다 (조회 회차가 달라도 같은 오류는 같은 값).
- 정규화 규칙 버전을 `error_groups.normalization_rule_version` 에 저장한다.

테스트는 정규화 레코드(`LogRecord`) fixture 기준으로 작성한다.
"""

#: 정규화 규칙 버전. 규칙을 고치면 반드시 올린다.
NORMALIZATION_RULE_VERSION = "v1"
