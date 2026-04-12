"""SQLite store for fetched items, scoring, and dedup."""
import sqlite3
import hashlib
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "digest.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    category TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    summary TEXT,
    published_at TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
    score REAL,
    title_zh TEXT,
    summary_en TEXT,
    summary_zh TEXT,
    why_care TEXT,
    why_care_zh TEXT,
    sent_in_issue TEXT
);
CREATE INDEX IF NOT EXISTS idx_score ON items(score);
CREATE INDEX IF NOT EXISTS idx_sent ON items(sent_in_issue);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def item_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def upsert_item(source, category, title, url, summary, published_at):
    iid = item_id(url)
    with conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO items
               (id, source, category, title, url, summary, published_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (iid, source, category, title, url, summary, published_at),
        )
    return iid


def unscored_items(limit=200):
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM items WHERE score IS NULL ORDER BY fetched_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_score(iid, score):
    with conn() as c:
        c.execute("UPDATE items SET score = ? WHERE id = ?", (score, iid))


def top_candidates(limit=20, min_score=6.0, max_age_hours=48):
    with conn() as c:
        rows = c.execute(
            """SELECT * FROM items
               WHERE sent_in_issue IS NULL
                 AND score >= ?
                 AND fetched_at >= datetime('now', ? || ' hours')
               ORDER BY score DESC, fetched_at DESC
               LIMIT ?""",
            (min_score, f"-{max_age_hours}", limit),
        ).fetchall()
    return [dict(r) for r in rows]


def attach_summary(iid, title_zh, summary_en, summary_zh, why_care, why_care_zh):
    with conn() as c:
        c.execute(
            "UPDATE items SET title_zh=?, summary_en=?, summary_zh=?, why_care=?, why_care_zh=? WHERE id=?",
            (title_zh, summary_en, summary_zh, why_care, why_care_zh, iid),
        )


def mark_sent(item_ids, issue_id):
    with conn() as c:
        c.executemany(
            "UPDATE items SET sent_in_issue = ? WHERE id = ?",
            [(issue_id, i) for i in item_ids],
        )
