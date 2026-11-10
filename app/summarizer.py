"""한 시간 동안 들어온 기사를 테마(주제)별로 중요한 소식만 골라 요약해 저장한다.

- 테마마다 중요도에 따라 0~3건
- 최근 12시간 안에 이미 요약한 소식과 같으면 건너뛴다
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta

from pydantic import BaseModel

from . import claude, config, db, similar
from .topics import TOPICS, classify

# 로그 기록용
log = logging.getLogger(__name__)

# 한 시간에 요약할 최대 기사 수 (넘으면 최신 기사만)
MAX_ARTICLES_PER_HOUR = 400
# 테마마다 최대 요약 수
MAX_PER_TOPIC = 3
# 중복 확인에 쓰는 지난 요약 범위(시간)
LOOKBACK_HOURS = 12
# 기본 모드: 이 정도 기사가 나온 테마는 겹치는 소식이 없어도 대표 소식 1건을 보여준다
BUSY_TOPIC_ARTICLES = 8
# 요약 방식이 바뀌면 올린다. 최근 요약 중 옛 방식으로 만든 것은 다시 만든다.
VERSION = "v2"


class DigestItem(BaseModel):
    """시간별 요약 한 건 (Claude 응답 형식)."""
    topic: str
    title: str
    summary: str
    article_ids: list[int]


class Digest(BaseModel):
    """한 시간의 요약 목록 (Claude 응답 형식)."""
    items: list[DigestItem]


# Claude에게 주는 역할 설명
SYSTEM = (
    "당신은 한국 경제신문의 데스크 기자입니다. 한 시간 동안 들어온 기사 목록을 받아 "
    "테마별로 정말 중요한 뉴스만 골라 요약합니다. 같은 사건을 다룬 기사는 하나로 묶고, "
    "기사에 없는 사실은 쓰지 않습니다."
)


def _prompt(articles: list[dict], start: datetime, previous: list[str]) -> str:
    """Claude에게 보낼 요청문: 규칙 + 이미 요약한 소식 + 그 시간의 기사 목록."""
    lines = [
        f"{start:%Y-%m-%d %H}:00 ~ {start + timedelta(hours=1):%H}:00 (한국시간)에 들어온 기사 {len(articles)}건입니다.",
        "",
        f"테마는 다음 중에서만 고르세요: {', '.join(TOPICS)}",
        f"테마마다 중요도 순으로 0~{MAX_PER_TOPIC}개만 고르세요. 중요한 소식이 없으면 그 테마는 0개여도 됩니다.",
        "중요도는 시장·경제·사회에 미치는 영향, 여러 언론이 다룬 정도, 속보 여부로 판단하세요.",
        "인사·부고·행사 안내·단순 홍보성 기사는 고르지 마세요.",
        "아래 '이미 요약한 소식'과 같은 소식이면 건너뛰세요. 단, 수치나 결정이 바뀌는 등 큰 새 진전이 있으면 그 진전을 중심으로 요약하세요.",
        "항목마다 다음을 채워주세요.",
        "- topic: 테마",
        "- title: 핵심을 담은 한국어 제목 (40자 이내)",
        "- summary: 정확히 3문장의 한국어 요약. 숫자와 주체를 구체적으로.",
        "- article_ids: 이 요약에 사용한 기사 번호 목록",
        "",
        "이미 요약한 소식 (최근 12시간):",
    ]
    lines += [f"- {t}" for t in previous[:150]] or ["- (없음)"]
    lines += ["", "기사 목록 ([번호] (테마) 제목 — 내용):"]
    for a in articles:
        lines.append(f"[{a['id']}] ({a['topic']}) {a['title']} — {a['description'][:200]}")
    return "\n".join(lines)


def _sentences(text: str, n: int = 3) -> str:
    """앞에서부터 n문장만 남긴다."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?다])\s+", text) if p.strip()]
    return " ".join(parts[:n]) if parts else text


def _useful_description(a: dict) -> str:
    """속보처럼 소개문이 '(서울=연합뉴스)' 정도뿐이면 빈 문자열."""
    from .sources import _BYLINE

    d = _BYLINE.sub("", a["description"], count=1).strip()
    return d if len(d) >= 15 else ""


def _already_covered(title: str, previous: list[str]) -> bool:
    """최근에 이미 요약한 소식과 같은지."""
    return any(similar.same_story(title, p) for p in previous)


def basic_digest(articles: list[dict], previous: list[str] | None = None) -> list[dict]:
    """Claude를 쓸 수 없을 때: 같은 소식끼리 묶어 많이 보도된 소식(속보·종합 우대)을 테마별 0~3건 고른다."""
    previous = previous or []
    articles = [a for a in articles if not similar.is_minor(a["title"])]
    groups = similar.cluster(sorted(articles, key=lambda a: a["published_at"]))
    by_topic: dict[str, list[tuple[float, list[dict]]]] = {}
    topic_counts = Counter(a["topic"] for a in articles)
    for g in groups:
        topic = Counter(a["topic"] for a in g).most_common(1)[0][0]
        if topic not in TOPICS:
            continue
        # 점수: 다룬 기사 수 + 속보·종합 가산점. 소수점 아래는 같은 테마 다른 기사와 얼마나 겹치는지(대표성)
        same_topic = [a for a in articles if a["topic"] == topic and a not in g]
        centrality = sum(similar.similarity(g[0]["title"], a["title"]) for a in same_topic) / max(1, len(same_topic))
        score = len(g) + max(similar.importance(a["title"]) for a in g) + centrality
        by_topic.setdefault(topic, []).append((score, g))

    items: list[dict] = []
    chosen_titles: list[str] = []
    for topic in TOPICS:
        ranked = sorted(by_topic.get(topic, []), key=lambda x: -x[0])
        picked = 0
        for score, g in ranked:
            if picked >= MAX_PER_TOPIC:
                break
            important = score >= 2
            fallback = picked == 0 and topic_counts[topic] >= BUSY_TOPIC_ARTICLES
            if not (important or fallback):
                break
            rep = max(g, key=lambda a: len(_useful_description(a)))  # 소개문이 가장 충실한 기사
            if _already_covered(rep["title"], previous) or any(
                similar.similarity(rep["title"], t) >= similar.THRESHOLD for t in chosen_titles
            ):
                continue
            items.append({
                "topic": topic,
                "title": rep["title"],
                "summary": _sentences(_useful_description(rep)),
                "article_ids": [a["id"] for a in g],
            })
            chosen_titles.append(rep["title"])
            picked += 1
    return items


def claude_digest(articles: list[dict], start: datetime, previous: list[str]) -> list[dict] | None:
    """Claude로 한 시간 요약. 실패하면 None (기본 모드로 대신)."""
    result = claude.ask(SYSTEM, _prompt(articles, start, previous), Digest, effort="low")
    if result is None:
        return None
    valid = {a["id"] for a in articles}
    items: list[dict] = []
    per_topic: Counter = Counter()
    for it in result.items:
        ids = [i for i in it.article_ids if i in valid]
        if not ids or not it.summary.strip():
            continue
        topic = it.topic if it.topic in TOPICS else classify(it.title + " " + it.summary)
        if per_topic[topic] >= MAX_PER_TOPIC:
            continue
        per_topic[topic] += 1
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
    previous = db.recent_summary_titles(start, LOOKBACK_HOURS)
    items, method = None, "basic"
    if articles:
        items = claude_digest(articles, start, previous)
        if items is not None:
            method = "claude"
    if items is None:
        items = basic_digest(articles, previous)
    db.save_digest(date, hour, f"{method}-{VERSION}", items)
    log.info("%s %02d시 요약 %d건 저장 (%s)", date, hour, len(items), method)
    return len(items)


def rebuild_outdated(hours: int = 48) -> int:
    """최근 hours시간 요약 중 옛 방식으로 만든 것을 시간 순서대로 다시 만든다."""
    since = db.now_kst() - timedelta(hours=hours)
    n = 0
    for d in db.digests_since(since):
        if d["method"].endswith(f"-{VERSION}"):
            continue
        start = datetime.fromisoformat(f"{d['date']}T{d['hour']:02d}:00:00").replace(tzinfo=config.KST)
        summarize_hour(start, force=True)
        n += 1
    return n
