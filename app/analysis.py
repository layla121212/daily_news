"""지수가 왜 움직이는지에 대한 해석과 근거 뉴스."""
from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from pydantic import BaseModel

from . import claude, config, db

log = logging.getLogger(__name__)

MARKET_TOPICS = ("증시·투자", "경제", "산업·기업", "국제")


class Point(BaseModel):
    title: str
    detail: str
    evidence_ids: list[int]


class Analysis(BaseModel):
    headline: str
    points: list[Point]


SYSTEM = (
    "당신은 한국 증시 애널리스트입니다. 현재 지수·선물 시세와 최근 뉴스만 근거로 "
    "지수가 왜 이렇게 움직이고 있는지 일반 독자가 이해하기 쉽게 설명합니다. "
    "뉴스에 근거가 없는 추측은 하지 않고, 매수·매도 권유는 하지 않습니다."
)

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "data": None}


def _fmt(d: dict) -> str:
    if d.get("price") is None:
        return f"{d['name']}: 데이터 없음"
    pct = d.get("change_pct")
    return f"{d['name']}: {d['price']:,.2f} ({pct:+.2f}%)" if pct is not None else f"{d['name']}: {d['price']:,.2f}"


def _recent_news(hours: int = 3, limit: int = 80) -> list[dict]:
    now = db.now_kst()
    rows = db.articles_between(now - timedelta(hours=hours), now + timedelta(minutes=1))
    rows = [r for r in rows if r["topic"] in MARKET_TOPICS] or rows
    return rows[-limit:]


def _prompt(market: dict, news: list[dict]) -> str:
    lines = ["[현재 시세]"]
    for d in market["indices"]:
        series = d.get("series") or []
        if series:
            lo = min(series, key=lambda x: x["v"])
            hi = max(series, key=lambda x: x["v"])
            lines.append(f"{_fmt(d)} / 장중 저점 {lo['v']:,.2f}({lo['t']}) 고점 {hi['v']:,.2f}({hi['t']})")
        else:
            lines.append(_fmt(d))
    lines += [_fmt(f) for f in market["futures"]["kr"] + market["futures"]["us"]]
    lines += ["", "[최근 3시간 뉴스] ([번호] 시각 제목 — 내용)"]
    for a in news:
        lines.append(f"[{a['id']}] {a['published_at'][11:16]} {a['title']} — {a['description'][:150]}")
    lines += [
        "",
        "코스피·코스닥이 지금처럼 움직이는 이유를 2~3가지로 정리해 주세요.",
        "- headline: 오늘 장을 한 줄로 요약 (35자 이내)",
        "- points: 각 이유의 title(한 문장), detail(2문장 이내 설명), evidence_ids(근거 뉴스 번호, 1개 이상)",
    ]
    return "\n".join(lines)


def _fallback(news: list[dict], reason: str) -> dict:
    evidence = [{"id": a["id"], "title": a["title"], "url": a["url"], "source": a["source"]}
                for a in news[-5:][::-1]]
    return {
        "method": "basic",
        "headline": "AI 분석을 사용할 수 없어요",
        "points": [{"title": reason, "detail": "대신 시장 관련 최신 뉴스를 보여드려요.", "evidence": evidence}],
    }


def build(market: dict) -> dict:
    news = _recent_news()
    if not config.has_claude_credentials():
        out = _fallback(news, "ANTHROPIC_API_KEY가 설정되지 않았어요.")
    elif not news:
        out = _fallback(news, "최근 3시간 동안 수집된 뉴스가 없어요.")
    else:
        result = claude.ask(SYSTEM, _prompt(market, news), Analysis, effort="medium")
        if result is None:
            out = _fallback(news, "AI 분석 생성에 실패했어요. 잠시 후 다시 시도할게요.")
        else:
            by_id = {a["id"]: a for a in news}
            points = []
            for p in result.points:
                ev = [{"id": i, "title": by_id[i]["title"], "url": by_id[i]["url"], "source": by_id[i]["source"]}
                      for i in dict.fromkeys(p.evidence_ids) if i in by_id]
                points.append({"title": p.title, "detail": p.detail, "evidence": ev})
            out = {"method": "claude", "headline": result.headline, "points": points}
    out["generated_at"] = db.now_kst().isoformat(timespec="seconds")
    return out


def get_analysis(market: dict) -> dict:
    """ANALYSIS_TTL 동안은 같은 분석을 재사용한다 (Claude 호출 비용·부하 방지)."""
    with _lock:
        if _cache["data"] is None or time.monotonic() - _cache["at"] >= config.ANALYSIS_TTL:
            _cache["data"] = build(market)
            # 실패한 경우에는 1분 뒤 다시 시도
            ttl_left = config.ANALYSIS_TTL if _cache["data"]["method"] == "claude" else 60
            _cache["at"] = time.monotonic() - (config.ANALYSIS_TTL - ttl_left)
        return _cache["data"]
