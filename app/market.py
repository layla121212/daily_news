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

# 로그 기록용
log = logging.getLogger(__name__)

# Yahoo 차트 API 주소 (지수 장중 차트·미국 선물)
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
# 네이버 증권 지수 API 주소 (코스피200 선물)
NAVER_INDEX = "https://m.stock.naver.com/api/index/{code}/basic"

# 국내 지수: (코드, 화면 이름, Yahoo 심볼)
INDICES = [("KOSPI", "코스피", "^KS11"), ("KOSDAQ", "코스닥", "^KQ11")]

# (표시 이름, 제공처, 코드)
KR_FUTURES = [("코스피200 선물", "naver", "FUT")]
# 미국 지수 선물: (화면 이름, 제공처, 심볼)
US_FUTURES = [
    ("S&P 500 선물", "yahoo", "ES=F"),
    ("나스닥100 선물", "yahoo", "NQ=F"),
    ("다우 선물", "yahoo", "YM=F"),
    ("러셀2000 선물", "yahoo", "RTY=F"),
]

# 여러 요청이 동시에 시세를 받지 않게 하는 잠금
_lock = threading.Lock()
# 마지막 시세와 받은 시각 (MARKET_CACHE_TTL 동안 재사용)
_cache: dict = {"at": 0.0, "data": None}


def _num(v) -> float | None:
    """'2,800.12' 같은 값을 숫자로. 안 되면 None."""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def parse_yahoo_chart(payload: dict) -> dict:
    """Yahoo 차트 응답 → 현재가, 전일 종가, 장중 시계열."""
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
    """네이버 지수 응답 → 현재가, 전일 종가."""
    price = _num(payload.get("closePrice"))
    change = _num(payload.get("compareToPreviousClosePrice"))
    prev = price - change if price is not None and change is not None else None
    return {"price": price, "prev_close": prev, "series": []}


def _with_change(d: dict) -> dict:
    """등락폭(change)과 등락률(change_pct)을 계산해 붙인다."""
    p, prev = d.get("price"), d.get("prev_close")
    if p is not None and prev:
        d["change"] = round(p - prev, 2)
        d["change_pct"] = round((p - prev) / prev * 100, 2)
    else:
        d["change"] = d["change_pct"] = None
    return d


def _fetch(client: httpx.Client, provider: str, code: str, intraday: bool = False) -> dict:
    """제공처(yahoo/naver)에서 시세 한 종목을 받아온다."""
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
    """지수·한국 선물·미국 선물 시세를 모두 새로 받아온다."""
    own = client is None
    client = client or httpx.Client(timeout=config.HTTP_TIMEOUT, headers={"User-Agent": config.USER_AGENT},
                                    follow_redirects=True)
    errors: list[str] = []

    def safe(name: str, provider: str, code: str, intraday: bool = False) -> dict:
        """한 종목 조회. 실패해도 빈 값으로 채우고 errors에 이름을 남긴다."""
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
