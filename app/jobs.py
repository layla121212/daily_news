"""백그라운드 작업: 뉴스 수집(1분마다)과 시간별 요약(매시 정각 직후)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from . import config, db, sources, summarizer

log = logging.getLogger(__name__)

# 정각 이후 이만큼 기다렸다가 직전 1시간을 요약한다 (늦게 올라오는 기사 대비)
SUMMARY_DELAY_MIN = 5


async def collect_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(sources.collect)
        except Exception:
            log.exception("뉴스 수집 중 오류")
        await asyncio.sleep(config.COLLECT_INTERVAL)


def catch_up(hours: int = 3) -> None:
    """서버가 꺼져 있던 동안 빠진 시간대를 요약한다."""
    now = db.now_kst().replace(minute=0, second=0, microsecond=0)
    for h in range(hours, 0, -1):
        summarizer.summarize_hour(now - timedelta(hours=h))


async def summary_loop() -> None:
    await asyncio.sleep(30)  # 첫 수집이 끝날 시간을 준다
    try:
        await asyncio.to_thread(catch_up)
    except Exception:
        log.exception("밀린 요약 처리 중 오류")
    while True:
        now = db.now_kst()
        nxt = (now + timedelta(hours=1)).replace(minute=SUMMARY_DELAY_MIN, second=0, microsecond=0)
        if now.minute < SUMMARY_DELAY_MIN:
            nxt = now.replace(minute=SUMMARY_DELAY_MIN, second=0, microsecond=0)
        await asyncio.sleep(max(1.0, (nxt - now).total_seconds()))
        try:
            prev_hour = db.now_kst().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
            await asyncio.to_thread(summarizer.summarize_hour, prev_hour)
        except Exception:
            log.exception("시간별 요약 중 오류")
