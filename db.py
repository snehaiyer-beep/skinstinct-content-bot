"""
SQLite storage for generated content. Schema carries voice_skill / channel
columns from day one so adding a second brand or a second Telegram channel
later (Section 9's scalability ask) is a new row, not a migration.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS content_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voice_skill TEXT NOT NULL,
    channel_id TEXT,
    category TEXT NOT NULL,
    platform TEXT NOT NULL,
    topic TEXT NOT NULL,
    subject_line TEXT,
    body TEXT NOT NULL,
    placeholders_json TEXT NOT NULL DEFAULT '[]',
    sources_json TEXT NOT NULL DEFAULT '[]',
    violations_json TEXT NOT NULL DEFAULT '[]',
    attempts INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'draft',  -- draft | approved | published | rejected | failed
    telegram_message_id TEXT,
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_content_items_status ON content_items(status);
CREATE INDEX IF NOT EXISTS idx_content_items_created ON content_items(created_at);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def save_draft(
    *,
    voice_skill: str,
    channel_id: str | None,
    category: str,
    platform: str,
    topic: str,
    subject_line: str | None,
    body: str,
    placeholders: list[str],
    sources: list[str],
    violations: list[str],
    attempts: int,
    status: str = "draft",
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO content_items (
                voice_skill, channel_id, category, platform, topic, subject_line,
                body, placeholders_json, sources_json, violations_json, attempts,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                voice_skill, channel_id, category, platform, topic, subject_line,
                body, json.dumps(placeholders), json.dumps(sources), json.dumps(violations),
                attempts, status, datetime.now(timezone.utc).isoformat(),
            ),
        )
        return cur.lastrowid


def update_status(content_id: int, status: str, telegram_message_id: str | None = None) -> None:
    with get_conn() as conn:
        if status == "published":
            conn.execute(
                "UPDATE content_items SET status=?, telegram_message_id=?, published_at=? WHERE id=?",
                (status, telegram_message_id, datetime.now(timezone.utc).isoformat(), content_id),
            )
        else:
            conn.execute(
                "UPDATE content_items SET status=?, telegram_message_id=COALESCE(?, telegram_message_id) WHERE id=?",
                (status, telegram_message_id, content_id),
            )


def get_item(content_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM content_items WHERE id=?", (content_id,)).fetchone()


def recent_summaries(voice_skill: str, platform: str, limit: int = 5) -> list[str]:
    """One-line summaries of recent published/approved pieces — feeds the pipeline's
    repetition-avoidance context injection."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT topic, subject_line, body FROM content_items
            WHERE voice_skill=? AND platform=? AND status IN ('approved', 'published')
            ORDER BY created_at DESC LIMIT ?
            """,
            (voice_skill, platform, limit),
        ).fetchall()
    summaries = []
    for row in rows:
        opening = row["body"].strip().splitlines()[0][:100] if row["body"].strip() else ""
        summaries.append(f"[{row['topic']}] opened with: {opening}")
    return summaries


def recent_items(limit: int = 10) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM content_items ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


def pending_drafts() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM content_items WHERE status='draft' ORDER BY created_at DESC"
        ).fetchall()
