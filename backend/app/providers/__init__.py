"""프로바이더 추상화.

- `logsource.LogSourceProvider` — 로그 소스 (첫 구현체: `app.loki`)
- `llm.LLMProvider` — LLM 프로바이더

원칙: **쿼리 언어는 추상화하지 않고, 연산과 결과 형식만 추상화한다.**
소스별 문법을 공통 DSL 로 번역하는 층은 만들지 않는다.

Phase 1 공유 계약. 수정 금지 (문제 발견 시 보고).
"""
