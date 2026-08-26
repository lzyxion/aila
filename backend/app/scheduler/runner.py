"""FastAPI lifespan 이 띄우는 스케줄러 루프.

`AILA_SCHEDULER_TICK_SECONDS`(기본 60) 주기로 `service.tick` 을 부른다.
tick 자체는 **동기 · 블로킹**(DB + Loki + LLM)이므로 `asyncio.to_thread` 로 넘긴다 —
이벤트 루프에서 직접 돌리면 tick 이 도는 동안 API 응답이 전부 멈춘다.

기동 직후 한 박자 쉬고 시작한다(`FIRST_TICK_DELAY_SECONDS`). 앱이 뜨자마자
정책을 다 돌리면 재기동이 그대로 조회 폭주가 된다.

**단일 uvicorn 워커 전제**다 — 워커마다 이 루프가 하나씩 뜨고, 겹침 방지 락은
프로세스 안에서만 유효하다 (`service.tick` docstring 참고).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.scheduler import service

logger = logging.getLogger(__name__)

#: 기동 직후 첫 tick 까지의 유예 (초).
FIRST_TICK_DELAY_SECONDS = 5.0


async def _loop(factory: sessionmaker[Session], interval_seconds: float) -> None:
    await asyncio.sleep(FIRST_TICK_DELAY_SECONDS)
    while True:
        try:
            report = await asyncio.to_thread(service.tick, factory)
            if report.results:
                logger.info("scheduler tick: 정책 %d 건 실행", len(report.results))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 루프는 어떤 예외로도 죽지 않는다
            # 죽으면 재기동 전까지 스케줄이 조용히 멈춘다. 로그만 남기고 계속 돈다.
            logger.exception("scheduler tick 이 예외로 끝났습니다. 다음 주기에 다시 시도합니다.")
        await asyncio.sleep(interval_seconds)


def start(factory: sessionmaker[Session]) -> asyncio.Task | None:
    """스케줄러 태스크를 띄운다. 꺼져 있으면 `None`."""
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("scheduler: AILA_SCHEDULER_ENABLED=false — 띄우지 않습니다.")
        return None
    interval = max(1, int(settings.scheduler_tick_seconds))
    logger.info("scheduler: %d 초 주기로 시작합니다 (단일 워커 전제).", interval)
    return asyncio.create_task(_loop(factory, float(interval)), name="aila-scheduler")


async def stop(task: asyncio.Task | None) -> None:
    """종료 시 태스크를 취소하고 정리를 기다린다."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


__all__ = ["FIRST_TICK_DELAY_SECONDS", "start", "stop"]
