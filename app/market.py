"""국내 지수(코스피·코스닥)와 한국·미국 지수 선물 시세.

여러 사용자가 짧은 주기로 새로고침해도 외부 시세 서버에는 MARKET_CACHE_TTL초에 한 번만 요청한다.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

import httpx

from . import config

log = logging.getLogger(__name__)

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
NAVER_INDEX = "https://m.stock.naver.com/api/index/{code}/basic"

INDICES = [("KOSPI", "코스피", "^KS11"), ("KOSDAQ", "코스닥", "^KQ11")]

# (표시 이름, 제공처, 코드)
KR_FUTURES = [("코스피200 선물", "naver", "FUT")]
US_FUTURES = [
    ("S&P 500 선물", "yahoo", "ES=F"),
    ("나스닥100 선물", "yahoo", "NQ=F"),
    ("다우 선물", "yahoo", "YM=F"),
    ("러셀2000 선물", "yahoo", "RTY=F"),
]

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "data": None}


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def parse_yahoo_chart(payload: dict) -> dict:
    res = payload["chart"]["result"][0]
    meta = res.get("meta", {})
    ts = res.get("timestamp") or []
    closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    series = [
        {"t": datetime.fromtimestamp(t, config.KST).strftime("%H:%M"), "v": round(c, 2)}
        for t, c in zip(ts, closes) if c is not None
    ]
    price = _num(meta.get("regularMarketPrice")) or (series[-1]["v"] if series else None)
    prev = _num(meta.get("chartPreviousClose") or meta.get("previousClose"))
    return {"price": price, "prev_close": prev, "series": series}


def parse_naver_index(payload: dict) -> dict:
    price = _num(payload.get("closePrice"))
    change = _num(payload.get("compareToPreviousClosePrice"))
    prev = price - change if price is not None and change is not None else None
    return {"price": price, "prev_close": prev, "series": []}


def _with_change(d: dict) -> dict:
    p, prev = d.get("price"), d.get("prev_close")
    if p is not None and prev:
        d["change"] = round(p - prev, 2)
        d["change_pct"] = round((p - prev) / prev * 100, 2)
    else:
        d["change"] = d["change_pct"] = None
    return d


def _fetch(client: httpx.Client, provider: str, code: str, intraday: bool = False) -> dict:
    if provider == "yahoo":
        params = {"interval": "5m", "range": "1d"} if intraday else {"interval": "1d", "range": "5d"}
        r = client.get(YAHOO_CHART.format(symbol=code), params=params)
        r.raise_for_status()
        return parse_yahoo_chart(r.json())
    if provider == "naver":
        r = client.get(NAVER_INDEX.format(code=code))
        r.raise_for_status()
        return parse_naver_index(r.json())
    raise ValueError(provider)


def fetch_market(client: httpx.Client | None = None) -> dict:
    own = client is None
    client = client or httpx.Client(timeout=config.HTTP_TIMEOUT, headers={"User-Agent": config.USER_AGENT},
                                    follow_redirects=True)
    errors: list[str] = []

    def safe(name: str, provider: str, code: str, intraday: bool = False) -> dict:
        try:
            d = _fetch(client, provider, code, intraday)
        except Exception as exc:
            log.warning("시세 조회 실패 %s(%s): %s", name, code, exc)
            errors.append(name)
            d = {"price": None, "prev_close": None, "series": []}
        d["name"] = name
        return _with_change(d)

    try:
        indices = []
        for key, name, sym in INDICES:
            d = safe(name, "yahoo", sym, intraday=True)
            d["code"] = key
            indices.append(d)
        kr = [safe(n, p, c) for n, p, c in KR_FUTURES]
        us = [safe(n, p, c) for n, p, c in US_FUTURES]
    finally:
        if own:
            client.close()
    for f in kr + us:
        f.pop("series", None)
    return {
        "updated_at": datetime.now(config.KST).isoformat(timespec="seconds"),
        "indices": indices,
        "futures": {"kr": kr, "us": us},
        "errors": errors,
    }


def get_market() -> dict:
    """캐시된 시세. TTL이 지났으면 한 요청만 외부에서 새로 받아온다."""
    with _lock:
        if _cache["data"] is None or time.monotonic() - _cache["at"] >= config.MARKET_CACHE_TTL:
            _cache["data"] = fetch_market()
            _cache["at"] = time.monotonic()
        return _cache["data"]
