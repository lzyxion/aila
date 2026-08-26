"""정책 스케줄러 (Phase 5).

- `service` — tick 로직 (due 판정 · 순차 실행 · 신규 fingerprint 자동 분석)
- `runner`  — FastAPI lifespan 이 띄우는 asyncio 백그라운드 루프

**단일 uvicorn 워커 전제**다. 자세한 근거는 `service.tick` docstring 참고.
"""
