"""웹 서버: 화면과 API."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date as date_cls

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import analysis, config, db, jobs, market
from .topics import TOPICS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC = config.BASE_DIR / "app" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    tasks = []
    if config.ENABLE_JOBS:
        tasks = [asyncio.create_task(jobs.collect_loop()), asyncio.create_task(jobs.summary_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="데일리 뉴스요약", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _check_date(d: str | None) -> str | None:
    if not d:
        return None
    try:
        return date_cls.fromisoformat(d).isoformat()
    except ValueError:
        raise HTTPException(400, "날짜는 YYYY-MM-DD 형식이어야 해요.")


def _check_topic(t: str | None) -> str | None:
    if not t:
        return None
    if t not in TOPICS:
        raise HTTPException(400, f"알 수 없는 주제: {t}")
    return t


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/topics")
def topics():
    return {"today": db.now_kst().date().isoformat(), "topics": TOPICS}


@app.get("/api/summaries")
def summaries(date: str | None = None, topic: str | None = None, q: str | None = Query(None, max_length=50)):
    """시간별 요약. 검색어가 없으면 기본은 오늘 날짜."""
    q = (q or "").strip() or None
    d = _check_date(date) or (None if q else db.now_kst().date().isoformat())
    return {"date": d, "items": db.search_summaries(date=d, topic=_check_topic(topic), q=q)}


@app.get("/api/articles")
def articles(date: str | None = None, topic: str | None = None, q: str | None = Query(None, max_length=50),
             limit: int = Query(50, ge=1, le=200)):
    """저장된 원문 기사 검색."""
    q = (q or "").strip() or None
    return {"items": db.search_articles(q=q, topic=_check_topic(topic), date=_check_date(date), limit=limit)}


@app.get("/api/live/market")
def live_market():
    return market.get_market()


@app.get("/api/live/analysis")
def live_analysis():
    return analysis.get_analysis(market.get_market())


@app.get("/api/live/news")
def live_news(after_id: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), topic: str | None = None):
    """실시간 뉴스. after_id를 주면 그 이후 새로 들어온 기사만 돌려준다."""
    items = db.latest_articles(after_id=after_id, limit=limit, topic=_check_topic(topic))
    return {"items": items, "server_time": db.now_kst().isoformat(timespec="seconds"),
            "collect_interval": config.COLLECT_INTERVAL}
