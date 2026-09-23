"""
Supabase-backed storage for generated content. Swapped in for the original
SQLite version so state survives between separate serverless invocations on
Vercel (a draft created in one webhook call must still be there when the
"Approve" button fires a later, separate call).

Table is created once via sql/schema.sql in the Supabase SQL editor — this
module only does CRUD through the REST client, never DDL.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache

from config import settings

logger = logging.getLogger(__name__)

TABLE = "skinstinct_content_items"


@lru_cache(maxsize=1)
def _client():
    settings.require_supabase()
    from supabase import create_client  # lazy import so tests don't need the package installed

    return create_client(settings.supabase_url, settings.supabase_service_key)


def init_db() -> None:
    """No-op: schema lives in sql/schema.sql, applied once via the Supabase
    SQL editor. Kept as a function so main.py/api don't need to know that."""
    settings.require_supabase()


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
    row = {
        "voice_skill": voice_skill,
        "channel_id": channel_id,
        "category": category,
        "platform": platform,
        "topic": topic,
        "subject_line": subject_line,
        "body": body,
        "placeholders": placeholders,
        "sources": sources,
        "violations": violations,
        "attempts": attempts,
        "status": status,
    }
    result = _client().table(TABLE).insert(row).execute()
    return result.data[0]["id"]


def update_status(content_id: int, status: str, telegram_message_id: str | None = None) -> None:
    update = {"status": status}
    if telegram_message_id is not None:
        update["telegram_message_id"] = telegram_message_id
    if status == "published":
        update["published_at"] = datetime.now(timezone.utc).isoformat()
    _client().table(TABLE).update(update).eq("id", content_id).execute()


def get_item(content_id: int) -> dict | None:
    result = _client().table(TABLE).select("*").eq("id", content_id).limit(1).execute()
    return result.data[0] if result.data else None


def recent_summaries(voice_skill: str, platform: str, limit: int = 5) -> list[str]:
    """One-line summaries of recent published/approved pieces — feeds the pipeline's
    repetition-avoidance context injection."""
    result = (
        _client()
        .table(TABLE)
        .select("topic,subject_line,body")
        .eq("voice_skill", voice_skill)
        .eq("platform", platform)
        .in_("status", ["approved", "published"])
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    summaries = []
    for row in result.data:
        body = (row.get("body") or "").strip()
        opening = body.splitlines()[0][:100] if body else ""
        summaries.append(f"[{row['topic']}] opened with: {opening}")
    return summaries


def recent_items(limit: int = 10) -> list[dict]:
    result = (
        _client().table(TABLE).select("*").order("created_at", desc=True).limit(limit).execute()
    )
    return result.data


def pending_drafts() -> list[dict]:
    result = (
        _client()
        .table(TABLE)
        .select("*")
        .eq("status", "draft")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data
