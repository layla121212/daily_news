"""한 시간 동안 들어온 기사를 주제별로 요약해 저장한다."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from pydantic import BaseModel

from . import claude, config, db
from .topics import TOPICS, classify

log = logging.getLogger(__name__)

MAX_ARTICLES_PER_HOUR = 400


class DigestItem(BaseModel):
    topic: str
    title: str
    summary: str
    article_ids: list[int]


class Digest(BaseModel):
    items: list[DigestItem]


SYSTEM = (
    "당신은 한국 경제신문의 데스크 기자입니다. 한 시간 동안 들어온 기사 목록을 받아 "
    "주제별 핵심 뉴스 요약을 만듭니다. 같은 사건을 다룬 기사는 하나로 묶고, "
    "기사에 없는 사실은 쓰지 않습니다."
)


def _prompt(articles: list[dict], start: datetime) -> str:
    lines = [
        f"{start:%Y-%m-%d %H}:00 ~ {start + timedelta(hours=1):%H}:00 (한국시간)에 들어온 기사 {len(articles)}건입니다.",
        "",
        f"주제는 다음 중에서만 고르세요: {', '.join(TOPICS)}",
        "각 주제마다 가장 중요한 뉴스를 1~3개 고르고, 기사가 없는 주제는 건너뛰세요.",
        "항목마다 다음을 채워주세요.",
        "- title: 핵심을 담은 한국어 제목 (40자 이내)",
        "- summary: 정확히 3문장의 한국어 요약. 숫자와 주체를 구체적으로.",
        "- article_ids: 이 요약에 사용한 기사 번호 목록",
        "",
        "기사 목록 ([번호] (주제) 제목 — 내용):",
    ]
    for a in articles:
        lines.append(f"[{a['id']}] ({a['topic']}) {a['title']} — {a['description'][:200]}")
    return "\n".join(lines)


def _sentences(text: str, n: int = 3) -> str:
    parts = [p.strip() for p in re.split(r"(?<=[.!?다])\s+", text) if p.strip()]
    return " ".join(parts[:n]) if parts else text


def basic_digest(articles: list[dict]) -> list[dict]:
    """Claude를 쓸 수 없을 때: 주제별 최신 기사 3건을 기사 소개문으로 보여준다."""
    by_topic: dict[str, list[dict]] = {}
    for a in sorted(articles, key=lambda x: x["published_at"], reverse=True):
        by_topic.setdefault(a["topic"], []).append(a)
    items = []
    for topic in TOPICS:
        for a in by_topic.get(topic, [])[:3]:
            items.append({
                "topic": topic,
                "title": a["title"],
                "summary": _sentences(a["description"]) or a["title"],
                "article_ids": [a["id"]],
            })
    return items


def claude_digest(articles: list[dict], start: datetime) -> list[dict] | None:
    result = claude.ask(SYSTEM, _prompt(articles, start), Digest, effort="low")
    if result is None:
        return None
    valid = {a["id"] for a in articles}
    items = []
    for it in result.items:
        ids = [i for i in it.article_ids if i in valid]
        if not ids or not it.summary.strip():
            continue
        topic = it.topic if it.topic in TOPICS else classify(it.title + " " + it.summary)
        items.append({"topic": topic, "title": it.title.strip(), "summary": it.summary.strip(), "article_ids": ids})
    return items


def summarize_hour(start: datetime, force: bool = False) -> int:
    """start 시각(정시)부터 1시간의 기사를 요약해 저장. 저장한 요약 수를 돌려준다."""
    start = start.astimezone(config.KST).replace(minute=0, second=0, microsecond=0)
    date, hour = start.strftime("%Y-%m-%d"), start.hour
    if not force and db.digest_done(date, hour):
        return 0
    articles = db.articles_between(start, start + timedelta(hours=1))
    if len(articles) > MAX_ARTICLES_PER_HOUR:
        log.warning("%s %d시 기사 %d건 중 최신 %d건만 요약", date, hour, len(articles), MAX_ARTICLES_PER_HOUR)
        articles = articles[-MAX_ARTICLES_PER_HOUR:]
    items, method = None, "basic"
    if articles:
        items = claude_digest(articles, start)
        if items is not None:
            method = "claude"
    if items is None:
        items = basic_digest(articles)
    db.save_digest(date, hour, method, items)
    log.info("%s %02d시 요약 %d건 저장 (%s)", date, hour, len(items), method)
    return len(items)
