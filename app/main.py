"""웹 서버: 화면과 API."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date as date_cls

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import analysis, config, daily, db, jobs, market
from .topics import TOPICS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# 화면 파일(index.html, app.js, style.css) 폴더
STATIC = config.BASE_DIR / "app" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 DB 준비와 백그라운드 작업 시작, 종료 시 작업 정리."""
    db.init()
    db.drop_unknown_topics(list(TOPICS))
    tasks = []
    if config.ENABLE_JOBS:
        tasks = [asyncio.create_task(jobs.collect_loop()), asyncio.create_task(jobs.summary_loop())]
    yield
    for t in tasks:
        t.cancel()


# 웹 서버 본체
app = FastAPI(title="데일리 뉴스요약", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _check_date(d: str | None) -> str | None:
    """날짜 문자열 확인 (YYYY-MM-DD). 비었으면 None, 틀리면 400 오류."""
    if not d:
        return None
    try:
        return date_cls.fromisoformat(d).isoformat()
    except ValueError:
        raise HTTPException(400, "날짜는 YYYY-MM-DD 형식이어야 해요.")


def _check_topic(t: str | None) -> str | None:
    """테마 이름 확인. 비었으면 None, 없는 테마면 400 오류."""
    if not t:
        return None
    if t not in TOPICS:
        raise HTTPException(400, f"알 수 없는 주제: {t}")
    return t


@app.get("/", include_in_schema=False)
def index():
    """첫 화면 (index.html)."""
    return FileResponse(STATIC / "index.html")


@app.get("/api/topics")
def topics():
    """테마와 추천 키워드, 오늘 날짜."""
    return {"today": db.now_kst().date().isoformat(), "topics": TOPICS}


@app.get("/api/summaries")
def summaries(date: str | None = None, topic: str | None = None, q: str | None = Query(None, max_length=50)):
    """시간별 요약. 검색어가 없으면 기본은 오늘 날짜."""
    q = (q or "").strip() or None
    d = _check_date(date) or (None if q else db.now_kst().date().isoformat())
    return {"date": d, "items": db.search_summaries(date=d, topic=_check_topic(topic), q=q)}


@app.get("/api/daily")
def daily_digest(date: str | None = None):
    """'요약' 탭: 하루 정리. 날짜가 없으면 가장 최근 정리를 돌려준다. 정리가 없으면 date만 채워 보낸다."""
    d = _check_date(date) or daily.latest_day()
    data = db.get_daily(d) if d else None
    return data or {"date": d, "overview": None, "items": []}


@app.get("/api/articles")
def articles(date: str | None = None, topic: str | None = None, q: str | None = Query(None, max_length=50),
             limit: int = Query(50, ge=1, le=200)):
    """저장된 원문 기사 검색."""
    q = (q or "").strip() or None
    return {"items": db.search_articles(q=q, topic=_check_topic(topic), date=_check_date(date), limit=limit)}


@app.get("/api/live/market")
def live_market():
    """실시간 탭: 지수·선물 시세 (캐시 사용)."""
    return market.get_market()


@app.get("/api/live/analysis")
def live_analysis():
    """실시간 탭: 지수 변화 분석 (캐시 사용)."""
    return analysis.get_analysis(market.get_market())


@app.get("/api/live/news")
def live_news(after_id: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=300), topic: str | None = None):
    """실시간 뉴스. after_id를 주면 그 이후 새로 들어온 기사만 돌려준다."""
    from .publish import live_news as build_live_news

    items = [a for a in build_live_news() if a["id"] > after_id and (not topic or a["topic"] == topic)][:limit]
    return {"items": items, "server_time": db.now_kst().isoformat(timespec="seconds"),
            "collect_interval": config.COLLECT_INTERVAL}
