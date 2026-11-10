"""SQLite 저장소: 원문 기사, 시간별 요약, 요약에 쓰인 기사 연결."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from . import config

# 테이블 구조 (처음 실행할 때 만들어진다)
SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL,
    published_at TEXT NOT NULL,   -- KST ISO 8601
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_topic ON articles(topic);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,           -- YYYY-MM-DD (KST)
    hour INTEGER NOT NULL,        -- 0~23, 이 시각부터 1시간 동안의 뉴스
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_summaries_date ON summaries(date, hour);

CREATE TABLE IF NOT EXISTS summary_articles (
    summary_id INTEGER NOT NULL REFERENCES summaries(id) ON DELETE CASCADE,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    PRIMARY KEY (summary_id, article_id)
);

-- 하루 정리 ('요약' 탭): 날짜별 한 줄, 그날 뉴스 상황 문장
CREATE TABLE IF NOT EXISTS daily_digests (
    date TEXT PRIMARY KEY,        -- YYYY-MM-DD (KST)
    method TEXT NOT NULL,         -- 'claude' 또는 'basic'
    overview TEXT NOT NULL,       -- 그날 뉴스 상황 정리
    created_at TEXT NOT NULL
);

-- 하루 정리에 고른 중요 기사 (날짜별 5~10건)
CREATE TABLE IF NOT EXISTS daily_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL REFERENCES daily_digests(date) ON DELETE CASCADE,
    rank INTEGER NOT NULL,        -- 중요도 순서 (1이 가장 중요)
    sentiment TEXT NOT NULL,      -- 긍정 / 중립 / 부정
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    article_ids TEXT NOT NULL     -- 근거 기사 id 목록 (JSON 배열)
);
CREATE INDEX IF NOT EXISTS idx_daily_items_date ON daily_items(date, rank);

-- 요약이 끝난 시간대 기록 (같은 시간대를 두 번 요약하지 않도록)
CREATE TABLE IF NOT EXISTS digests (
    date TEXT NOT NULL,
    hour INTEGER NOT NULL,
    method TEXT NOT NULL,         -- 'claude' 또는 'basic'
    created_at TEXT NOT NULL,
    PRIMARY KEY (date, hour)
);
"""


@dataclass
class Article:
    """수집한 기사 한 건 (저장 전 형태)."""
    url: str
    title: str
    description: str
    source: str
    topic: str
    published_at: datetime


def _path() -> Path:
    """SQLite 파일 경로 (설정값)."""
    return Path(config.DB_PATH)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """DB 연결을 열고, 작업이 끝나면 저장(commit)하고 닫는다."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    """테이블이 없으면 만든다 (서버·사이트 생성 시작 때 호출)."""
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)


def now_kst() -> datetime:
    """지금 시각 (한국시간)."""
    return datetime.now(config.KST)


def _like(q: str) -> str:
    """LIKE 검색용 패턴. %, _ 같은 특수문자는 글자 그대로 찾도록 바꾼다."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ---------- 기사 ----------

def insert_articles(items: list[Article]) -> int:
    """새 기사만 저장하고 저장된 개수를 돌려준다(같은 URL은 건너뜀)."""
    fetched = now_kst().isoformat(timespec="seconds")
    added = 0
    with connect() as conn:
        for a in items:
            cur = conn.execute(
                "INSERT OR IGNORE INTO articles(url,title,description,source,topic,published_at,fetched_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (a.url, a.title, a.description, a.source, a.topic,
                 a.published_at.astimezone(config.KST).isoformat(timespec="seconds"), fetched),
            )
            added += cur.rowcount
    return added


def articles_between(start: datetime, end: datetime) -> list[dict]:
    """start 이상 end 미만에 발행된 기사 (시간순)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM articles WHERE published_at >= ? AND published_at < ? ORDER BY published_at",
            (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
        ).fetchall()
    return [dict(r) for r in rows]


def latest_articles(after_id: int = 0, limit: int = 30, topic: str | None = None) -> list[dict]:
    """실시간 뉴스: after_id보다 새로 들어온 기사(최신순)."""
    sql = "SELECT * FROM articles WHERE id > ?"
    args: list = [after_id]
    if topic:
        sql += " AND topic = ?"
        args.append(topic)
    sql += " ORDER BY published_at DESC, id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def get_articles(ids: list[int]) -> list[dict]:
    """id 목록에 해당하는 기사들."""
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM articles WHERE id IN ({marks})", ids).fetchall()
    return [dict(r) for r in rows]


def search_articles(q: str | None = None, topic: str | None = None, date: str | None = None,
                    limit: int = 50) -> list[dict]:
    """원문 기사 검색 (검색어·테마·날짜, 최신순)."""
    sql = "SELECT * FROM articles WHERE 1=1"
    args: list = []
    if q:
        sql += " AND (title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')"
        args += [_like(q), _like(q)]
    if topic:
        sql += " AND topic = ?"
        args.append(topic)
    if date:
        sql += " AND substr(published_at,1,10) = ?"
        args.append(date)
    sql += " ORDER BY published_at DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ---------- 요약 ----------

def digest_done(date: str, hour: int) -> bool:
    """그 시간대의 시간별 요약이 이미 끝났는지."""
    with connect() as conn:
        return conn.execute("SELECT 1 FROM digests WHERE date=? AND hour=?", (date, hour)).fetchone() is not None


def save_digest(date: str, hour: int, method: str, items: list[dict]) -> None:
    """items: [{topic, title, summary, article_ids}]"""
    created = now_kst().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("DELETE FROM summaries WHERE date=? AND hour=?", (date, hour))
        for it in items:
            cur = conn.execute(
                "INSERT INTO summaries(date,hour,topic,title,summary,created_at) VALUES (?,?,?,?,?,?)",
                (date, hour, it["topic"], it["title"], it["summary"], created),
            )
            sid = cur.lastrowid
            for aid in dict.fromkeys(it.get("article_ids", [])):
                conn.execute("INSERT OR IGNORE INTO summary_articles(summary_id,article_id) VALUES (?,?)", (sid, aid))
        conn.execute(
            "INSERT OR REPLACE INTO digests(date,hour,method,created_at) VALUES (?,?,?,?)",
            (date, hour, method, created),
        )


def recent_summary_titles(before: datetime, hours: int = 12) -> list[str]:
    """before 이전 hours시간 동안의 요약 제목과, 그 요약에 쓰인 기사 제목들."""
    since = before - timedelta(hours=hours)
    lo, hi = since.strftime("%Y-%m-%dT%H"), before.strftime("%Y-%m-%dT%H")
    key = "s.date || printf('T%02d', s.hour)"
    with connect() as conn:
        rows = conn.execute(
            f"SELECT s.title FROM summaries s WHERE {key} >= ? AND {key} < ? "
            f"UNION ALL SELECT a.title FROM summaries s JOIN summary_articles sa ON sa.summary_id = s.id "
            f"JOIN articles a ON a.id = sa.article_id WHERE {key} >= ? AND {key} < ?",
            (lo, hi, lo, hi),
        ).fetchall()
    return [r[0] for r in rows]


def digests_since(since: datetime) -> list[dict]:
    """since 이후 시간별 요약 기록 (시간순)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM digests WHERE date || printf('T%02d', hour) >= ? ORDER BY date, hour",
            (since.strftime("%Y-%m-%dT%H"),),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- 하루 정리 ('요약' 탭) ----------

def daily_done(date: str) -> bool:
    """그날의 하루 정리가 이미 있는지."""
    with connect() as conn:
        return conn.execute("SELECT 1 FROM daily_digests WHERE date=?", (date,)).fetchone() is not None


def save_daily(date: str, method: str, overview: str, items: list[dict]) -> None:
    """하루 정리 저장 (같은 날짜는 덮어쓴다). items: [{sentiment, topic, title, summary, article_ids}]"""
    created = now_kst().isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute("DELETE FROM daily_items WHERE date=?", (date,))
        conn.execute(
            "INSERT OR REPLACE INTO daily_digests(date,method,overview,created_at) VALUES (?,?,?,?)",
            (date, method, overview, created),
        )
        for rank, it in enumerate(items, start=1):
            conn.execute(
                "INSERT INTO daily_items(date,rank,sentiment,topic,title,summary,article_ids) VALUES (?,?,?,?,?,?,?)",
                (date, rank, it["sentiment"], it["topic"], it["title"], it["summary"],
                 json.dumps(it.get("article_ids", []))),
            )


def get_daily(date: str) -> dict | None:
    """화면에 보여줄 하루 정리. 기사 목록에는 원문 제목·링크를 붙인다. 없으면 None."""
    with connect() as conn:
        head = conn.execute("SELECT * FROM daily_digests WHERE date=?", (date,)).fetchone()
        if head is None:
            return None
        rows = conn.execute("SELECT * FROM daily_items WHERE date=? ORDER BY rank", (date,)).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        ids = json.loads(item.pop("article_ids"))
        arts = {a["id"]: a for a in get_articles(ids)}
        item["articles"] = [
            {k: arts[i][k] for k in ("id", "title", "url", "source", "published_at")} for i in ids if i in arts
        ]
        items.append(item)
    return dict(head) | {"items": items}


def daily_dates() -> list[str]:
    """하루 정리가 있는 날짜 목록 (최신순)."""
    with connect() as conn:
        return [r[0] for r in conn.execute("SELECT date FROM daily_digests ORDER BY date DESC")]


def drop_unknown_topics(topics: list[str]) -> int:
    """더 이상 쓰지 않는 주제(예: 삭제한 '사회')의 요약과 기사를 지운다.
    요약에 연결된 기사는 다른 주제 요약에도 쓰였으면 남긴다."""
    marks = ",".join("?" * len(topics))
    with connect() as conn:
        n = conn.execute(f"DELETE FROM summaries WHERE topic NOT IN ({marks})", topics).rowcount
        n += conn.execute(
            f"DELETE FROM articles WHERE topic NOT IN ({marks}) "
            "AND id NOT IN (SELECT article_id FROM summary_articles)", topics,
        ).rowcount
    return n


def search_summaries(date: str | None = None, topic: str | None = None, q: str | None = None,
                     limit: int = 300) -> list[dict]:
    """요약 검색. q는 요약 본문뿐 아니라 요약에 쓰인 원문 기사 제목·내용에서도 찾는다."""
    sql = "SELECT s.* FROM summaries s WHERE 1=1"
    args: list = []
    if date:
        sql += " AND s.date = ?"
        args.append(date)
    if topic:
        sql += " AND s.topic = ?"
        args.append(topic)
    if q:
        sql += (" AND (s.title LIKE ? ESCAPE '\\' OR s.summary LIKE ? ESCAPE '\\' OR EXISTS ("
                "SELECT 1 FROM summary_articles sa JOIN articles a ON a.id = sa.article_id "
                "WHERE sa.summary_id = s.id AND (a.title LIKE ? ESCAPE '\\' OR a.description LIKE ? ESCAPE '\\')))")
        args += [_like(q)] * 4
    sql += " ORDER BY s.date DESC, s.hour DESC, s.id LIMIT ?"
    args.append(limit)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        for r in rows:
            r["articles"] = [dict(a) for a in conn.execute(
                "SELECT a.id, a.title, a.url, a.source, a.published_at FROM summary_articles sa "
                "JOIN articles a ON a.id = sa.article_id WHERE sa.summary_id = ? ORDER BY a.published_at",
                (r["id"],),
            ).fetchall()]
    return rows
