"""언론사 RSS에서 기사를 가져온다."""
from __future__ import annotations

import calendar
import html
import json
import logging
import re
from datetime import datetime, timezone

import feedparser
import httpx

from . import config, db
from .topics import classify

# 로그 기록용
log = logging.getLogger(__name__)

# HTML 태그
_TAG = re.compile(r"<[^>]+>")
# 기사 첫머리의 "(서울=연합뉴스) 홍길동 기자 = ", "[한국경제] ", "홍길동 기자 = " 같은 머리말
_BYLINE = re.compile(
    r"^\s*(?:[\(\[][^\)\]]{1,30}[\)\]]\s*)?"
    r"(?:(?:[가-힣]{2,4}[\s·,]*){1,4}(?:기자|특파원)(?:[\s·,]+(?:[가-힣]{2,4}[\s·,]*){1,4}(?:기자|특파원))*\s*=\s*)?"
)


def _clean(text: str | None) -> str:
    """HTML 태그·특수문자를 없애고 공백을 정리한다."""
    text = html.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def strip_byline(text: str) -> str:
    """소개문 앞의 '(서울=연합뉴스) 홍길동 기자 =' 같은 머리말을 지운다."""
    stripped = _BYLINE.sub("", text, count=1).strip()
    return stripped or text


def load_feeds() -> list[dict]:
    """feeds.json에서 수집할 RSS 목록을 읽는다."""
    with open(config.FEEDS_PATH, encoding="utf-8") as f:
        return json.load(f)


def parse_feed(raw: bytes | str, source: str, topic: str | None) -> list[db.Article]:
    """RSS 내용 → 기사 목록. 테마는 피드 기본값과 내용으로 정한다."""
    parsed = feedparser.parse(raw)
    out: list[db.Article] = []
    for e in parsed.entries:
        url = (e.get("link") or "").strip()
        title = _clean(e.get("title"))
        if not url or not title:
            continue
        desc = strip_byline(_clean(e.get("summary") or e.get("description")))[:600]
        st = e.get("published_parsed") or e.get("updated_parsed")
        published = (datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
                     if st else datetime.now(timezone.utc))
        out.append(db.Article(
            url=url, title=title, description=desc, source=source,
            topic=classify(f"{title} {desc}", default=topic), published_at=published,
        ))
    return out


def fetch_all(client: httpx.Client | None = None) -> list[db.Article]:
    """모든 피드를 돌며 기사를 모은다. 한 피드가 실패해도 계속한다."""
    own = client is None
    client = client or httpx.Client(timeout=config.HTTP_TIMEOUT, headers={"User-Agent": config.USER_AGENT},
                                    follow_redirects=True)
    articles: list[db.Article] = []
    try:
        for feed in load_feeds():
            try:
                r = client.get(feed["url"])
                r.raise_for_status()
                articles += parse_feed(r.content, feed.get("name", ""), feed.get("topic"))
            except Exception as exc:  # 한 피드 실패가 전체 수집을 막지 않도록
                log.warning("피드 수집 실패 %s: %s", feed.get("url"), exc)
    finally:
        if own:
            client.close()
    return articles


def collect() -> int:
    """피드를 한 번 돌고 새 기사 수를 돌려준다."""
    added = db.insert_articles(fetch_all())
    log.info("새 기사 %d건 저장", added)
    return added
