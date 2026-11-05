from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import analysis, claude, config, db, market, sources, summarizer
from app.topics import classify

KST = config.KST

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>t</title>
<item><title>한은, 기준금리 동결…연내 인하 시사</title><link>https://ex.com/1</link>
<description><![CDATA[<p>한국은행이 기준금리를 동결했다. 물가 둔화가 근거다. 시장은 인하를 기대한다.</p>]]></description>
<pubDate>Thu, 05 Nov 2026 00:10:00 GMT</pubDate></item>
<item><title>서울 아파트 전세가 상승</title><link>https://ex.com/2</link>
<description>전세 매물이 줄었다.</description><pubDate>Thu, 05 Nov 2026 00:20:00 GMT</pubDate></item>
<item><title></title><link>https://ex.com/empty</link></item>
</channel></rss>"""


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "news.db")
    monkeypatch.setattr(config, "ENABLE_JOBS", False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    analysis._cache.update(at=0.0, data=None)
    market._cache.update(at=0.0, data=None)
    db.init()


def _seed(hour_start: datetime) -> list[int]:
    items = [
        db.Article("https://ex.com/a", "코스피, 반도체 강세에 상승", "외국인이 삼성전자를 순매수했다.", "예시", "증시·투자",
                   hour_start + timedelta(minutes=5)),
        db.Article("https://ex.com/b", "한은 기준금리 동결", "한국은행이 금리를 동결했다. 물가가 안정됐다.", "예시", "경제",
                   hour_start + timedelta(minutes=30)),
        db.Article("https://ex.com/c", "다른 시간 기사", "범위 밖", "예시", "사회", hour_start + timedelta(hours=2)),
    ]
    assert db.insert_articles(items) == 3
    assert db.insert_articles(items[:1]) == 0  # 같은 URL은 다시 저장하지 않음
    return sorted(a["id"] for a in db.search_articles())  # 시간순: a, b, c


def test_parse_feed_cleans_html_and_classifies():
    arts = sources.parse_feed(RSS, "예시일보", "경제")
    assert [a.url for a in arts] == ["https://ex.com/1", "https://ex.com/2"]
    assert "<p>" not in arts[0].description
    assert arts[0].topic == "경제"
    assert arts[1].topic == "부동산"  # 경제 피드여도 내용이 부동산이면 부동산
    assert arts[0].published_at.astimezone(KST).hour == 9


def test_classify_default_when_no_keyword():
    assert classify("오늘의 날씨", default="사회") == "사회"


def test_basic_digest_saved_and_searchable():
    start = datetime(2026, 11, 5, 9, tzinfo=KST)
    _seed(start)
    assert summarizer.summarize_hour(start) == 2
    assert summarizer.summarize_hour(start) == 0  # 이미 요약된 시간대는 건너뜀
    rows = db.search_summaries(date="2026-11-05")
    assert {r["topic"] for r in rows} == {"증시·투자", "경제"}
    assert all(r["hour"] == 9 and len(r["articles"]) == 1 for r in rows)
    # 요약 본문에 없는 단어도 원문 기사 내용으로 찾을 수 있다
    hit = db.search_summaries(q="삼성전자")
    assert len(hit) == 1 and hit[0]["topic"] == "증시·투자"
    assert db.search_summaries(q="없는단어") == []
    assert db.search_summaries(topic="경제", date="2026-11-04") == []


def test_like_wildcards_are_escaped():
    _seed(datetime(2026, 11, 5, 9, tzinfo=KST))
    assert db.search_articles(q="%") == []


def test_claude_digest_filters_invalid_ids(monkeypatch):
    start = datetime(2026, 11, 5, 9, tzinfo=KST)
    ids = _seed(start)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    def fake_ask(system, prompt, schema, **kw):
        assert f"[{ids[0]}]" in prompt and f"[{ids[2]}]" not in prompt  # 범위 밖 기사는 제외
        return summarizer.Digest(items=[
            summarizer.DigestItem(topic="증시·투자", title="반도체가 이끈 코스피", summary="가. 나. 다.",
                                  article_ids=[ids[0], ids[1], 9999]),
            summarizer.DigestItem(topic="없는주제", title="금리 동결", summary="한국은행 금리 동결.", article_ids=[ids[1]]),
            summarizer.DigestItem(topic="경제", title="근거 없음", summary="x", article_ids=[9999]),
        ])

    monkeypatch.setattr(claude, "ask", fake_ask)
    assert summarizer.summarize_hour(start) == 2
    rows = db.search_summaries(date="2026-11-05")
    by_title = {r["title"]: r for r in rows}
    assert len(by_title["반도체가 이끈 코스피"]["articles"]) == 2
    assert by_title["금리 동결"]["topic"] == "경제"  # 잘못된 주제는 내용으로 다시 분류


def test_claude_failure_falls_back_to_basic(monkeypatch):
    start = datetime(2026, 11, 5, 9, tzinfo=KST)
    _seed(start)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(claude, "ask", lambda *a, **k: None)
    assert summarizer.summarize_hour(start) == 2


def test_parse_yahoo_and_naver():
    payload = {"chart": {"result": [{
        "meta": {"regularMarketPrice": 2810.5, "chartPreviousClose": 2786.4},
        "timestamp": [1793836800, 1793837100, 1793837400],
        "indicators": {"quote": [{"close": [2790.0, None, 2810.5]}]},
    }]}}
    d = market._with_change(market.parse_yahoo_chart(payload))
    assert d["price"] == 2810.5 and len(d["series"]) == 2
    assert d["change"] == 24.1 and d["change_pct"] == 0.86
    n = market._with_change(market.parse_naver_index({"closePrice": "372.85", "compareToPreviousClosePrice": "-2.75"}))
    assert n["prev_close"] == pytest.approx(375.6) and n["change_pct"] < 0


def test_market_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(market, "fetch_market", lambda: calls.append(1) or {"ok": len(calls)})
    monkeypatch.setattr(config, "MARKET_CACHE_TTL", 60)
    for _ in range(5):
        market.get_market()
    assert len(calls) == 1


def test_analysis_without_key_shows_news(monkeypatch):
    now = db.now_kst()
    db.insert_articles([db.Article("https://ex.com/x", "코스피 상승", "외국인 매수", "예시", "증시·투자", now - timedelta(minutes=10))])
    out = analysis.get_analysis({"indices": [], "futures": {"kr": [], "us": []}})
    assert out["method"] == "basic" and out["points"][0]["evidence"][0]["title"] == "코스피 상승"


def test_analysis_with_claude(monkeypatch):
    now = db.now_kst()
    db.insert_articles([db.Article("https://ex.com/x", "반도체 수출 증가", "HBM 수요", "예시", "산업·기업", now - timedelta(minutes=10))])
    aid = db.search_articles()[0]["id"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(claude, "ask", lambda *a, **k: analysis.Analysis(
        headline="반도체 강세", points=[analysis.Point(title="수출 호조", detail="수출이 늘었다.", evidence_ids=[aid, 777])]))
    m = {"indices": [{"name": "코스피", "price": 2800.0, "change_pct": 0.5,
                      "series": [{"t": "09:00", "v": 2790.0}, {"t": "10:00", "v": 2800.0}]}],
         "futures": {"kr": [], "us": [{"name": "S&P 500 선물", "price": None}]}}
    out = analysis.get_analysis(m)
    assert out["method"] == "claude"
    assert [e["id"] for e in out["points"][0]["evidence"]] == [aid]


def test_api_endpoints(monkeypatch):
    from app.main import app

    start = db.now_kst().replace(minute=0, second=0, microsecond=0)
    _seed(start)
    summarizer.summarize_hour(start)
    with TestClient(app) as c:
        assert "경제" in c.get("/api/topics").json()["topics"]
        r = c.get("/api/summaries").json()
        assert r["date"] == start.date().isoformat() and len(r["items"]) == 2
        assert c.get("/api/summaries", params={"q": "금리"}).json()["date"] is None
        assert c.get("/api/summaries", params={"date": "2026/11/05"}).status_code == 400
        assert c.get("/api/summaries", params={"topic": "스포츠"}).status_code == 400
        news = c.get("/api/live/news").json()["items"]
        assert len(news) == 3
        newest = max(a["id"] for a in news)
        assert c.get("/api/live/news", params={"after_id": newest}).json()["items"] == []
        assert c.get("/api/articles", params={"q": "반도체"}).json()["items"][0]["url"] == "https://ex.com/a"
        assert "데일리" in c.get("/").text


def test_publish_exports_static_site(tmp_path, monkeypatch):
    import json

    from app import publish

    start = datetime(2026, 11, 5, 9, tzinfo=KST)
    _seed(start)
    summarizer.summarize_hour(start)
    mkt = {"indices": [], "futures": {"kr": [], "us": []}, "errors": [], "updated_at": "x"}
    ana = publish._analysis(tmp_path / "state" / "analysis.json", mkt)
    out = tmp_path / "site"
    out.mkdir()
    publish.export(out, mkt, ana)
    assert 'data-mode="static"' in (out / "index.html").read_text(encoding="utf-8")
    assert (out / "static" / "app.js").exists() and not (out / "static" / "index.html").exists()
    meta = json.loads((out / "data" / "meta.json").read_text(encoding="utf-8"))
    assert meta["summary_dates"] == ["2026-11-05"] and "경제" in meta["topics"]
    day = json.loads((out / "data" / "summaries" / "2026-11-05.json").read_text(encoding="utf-8"))
    assert len(day) == 2 and day[0]["articles"]
    assert len(json.loads((out / "data" / "live" / "news.json").read_text(encoding="utf-8"))) == 3


def test_publish_reuses_recent_analysis(tmp_path, monkeypatch):
    from app import publish

    calls = []
    monkeypatch.setattr(analysis, "build", lambda m: calls.append(1) or
                        {"method": "claude", "headline": "h", "points": [], "generated_at": db.now_kst().isoformat()})
    state = tmp_path / "a.json"
    publish._analysis(state, {})
    publish._analysis(state, {})
    assert len(calls) == 1


def test_market_hours():
    from app.publish import market_hours

    assert market_hours(datetime(2026, 11, 5, 10, tzinfo=KST))       # 목요일 10시
    assert not market_hours(datetime(2026, 11, 5, 20, tzinfo=KST))   # 장 마감 후
    assert not market_hours(datetime(2026, 11, 7, 10, tzinfo=KST))   # 토요일


@pytest.mark.parametrize("raw, expected", [
    ("(서울=연합뉴스) 이성한 기자 = 미국 증시가 폭락했다.", "미국 증시가 폭락했다."),
    ("(워싱턴=연합뉴스) 백나리 특파원 = 트럼프 대통령은", "트럼프 대통령은"),
    ("[한국경제] 코스피가 올랐다.", "코스피가 올랐다."),
    ("(서울=연합뉴스) 이상현 민선희 기자 = 캄보디아 취업 사기", "캄보디아 취업 사기"),
    ("(서울=연합뉴스) 김철수 기자·이영희 특파원 = 협상이 타결됐다.", "협상이 타결됐다."),
    ("(전남광주=연합뉴스) 기상청은 6일 풍랑주의보를", "기상청은 6일 풍랑주의보를"),
    ("한국은행이 기준금리를 동결했다.", "한국은행이 기준금리를 동결했다."),
])
def test_strip_byline(raw, expected):
    assert sources.strip_byline(raw) == expected
