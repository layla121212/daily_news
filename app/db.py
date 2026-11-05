"""SQLite 저장소: 원문 기사, 시간별 요약, 요약에 쓰인 기사 연결."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from . import config

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
    url: str
    title: str
    description: str
    source: str
    topic: str
    published_at: datetime


def _path() -> Path:
    return Path(config.DB_PATH)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
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
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)


def now_kst() -> datetime:
    return datetime.now(config.KST)


def _like(q: str) -> str:
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
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM articles WHERE id IN ({marks})", ids).fetchall()
    return [dict(r) for r in rows]


def search_articles(q: str | None = None, topic: str | None = None, date: str | None = None,
                    limit: int = 50) -> list[dict]:
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
