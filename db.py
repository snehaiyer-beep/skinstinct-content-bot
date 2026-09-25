"""
Supabase-backed storage: notes (raw ideas + their score), drafts (generated
content + its lifecycle), and voice_skills (personas, editable without a
redeploy). Swapped in for a local SQLite file so state survives between
separate serverless invocations on Vercel (a draft created in one webhook
call must still be there when "APPROVE" arrives as a later, separate call).

Tables are created once via sql/schema.sql + sql/002_notes_drafts_voice_skills.sql
in the Supabase SQL editor — this module only does CRUD through the REST
client, never DDL.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache

from config import settings

logger = logging.getLogger(__name__)

TABLE_DRAFTS = "skinstinct_drafts"
TABLE_NOTES = "skinstinct_notes"
TABLE_VOICE_SKILLS = "skinstinct_voice_skills"


@lru_cache(maxsize=1)
def _client():
    settings.require_supabase()
    from supabase import create_client  # lazy import so tests don't need the package installed

    return create_client(settings.supabase_url, settings.supabase_service_key)


def init_db() -> None:
    """No-op: schema lives in sql/, applied once via the Supabase SQL editor.
    Kept as a function so main.py/api don't need to know that."""
    settings.require_supabase()


# --------------------------------------------------------------- notes ----

def save_note(*, voice_skill: str, text: str) -> int:
    row = {"voice_skill": voice_skill, "text": text, "status": "pending"}
    result = _client().table(TABLE_NOTES).insert(row).execute()
    return result.data[0]["id"]


def update_note_score(note_id: int, *, score: int, reason: str, status: str) -> None:
    _client().table(TABLE_NOTES).update(
        {"score": score, "score_reason": reason, "status": status}
    ).eq("id", note_id).execute()


def get_note(note_id: int) -> dict | None:
    result = _client().table(TABLE_NOTES).select("*").eq("id", note_id).limit(1).execute()
    return result.data[0] if result.data else None


# -------------------------------------------------------------- drafts ----

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
    note_id: int | None = None,
    search_phrase: str | None = None,
    used_news_item: bool = False,
    news_item: dict | None = None,
    status: str = "pending",
) -> int:
    row = {
        "note_id": note_id,
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
        "search_phrase": search_phrase,
        "used_news_item": used_news_item,
        "status": status,
    }
    if news_item:
        row.update(
            {
                "news_headline": news_item.get("headline"),
                "news_source": news_item.get("source"),
                "news_date": news_item.get("date"),
                "news_link": news_item.get("link"),
                "news_summary": news_item.get("summary"),
            }
        )
    result = _client().table(TABLE_DRAFTS).insert(row).execute()
    return result.data[0]["id"]


def update_status(content_id: int, status: str, telegram_message_id: str | None = None) -> None:
    update = {"status": status}
    if telegram_message_id is not None:
        update["telegram_message_id"] = str(telegram_message_id)
    if status in ("approved", "rejected"):
        update["decided_at"] = datetime.now(timezone.utc).isoformat()
    if status == "published":
        update["published_at"] = datetime.now(timezone.utc).isoformat()
    _client().table(TABLE_DRAFTS).update(update).eq("id", content_id).execute()


def get_item(content_id: int) -> dict | None:
    result = _client().table(TABLE_DRAFTS).select("*").eq("id", content_id).limit(1).execute()
    return result.data[0] if result.data else None


def get_draft_by_telegram_message(message_id: str) -> dict | None:
    result = (
        _client()
        .table(TABLE_DRAFTS)
        .select("*")
        .eq("telegram_message_id", str(message_id))
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def recent_summaries(voice_skill: str, platform: str, limit: int = 5) -> list[str]:
    """One-line summaries of recent published/approved pieces — feeds the pipeline's
    repetition-avoidance context injection."""
    result = (
        _client()
        .table(TABLE_DRAFTS)
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
        _client()
        .table(TABLE_DRAFTS)
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


def pending_drafts() -> list[dict]:
    result = (
        _client()
        .table(TABLE_DRAFTS)
        .select("*")
        .eq("status", "pending")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data


# --------------------------------------------------------- voice skills ---

def get_voice_skill_content(name: str) -> str | None:
    result = (
        _client().table(TABLE_VOICE_SKILLS).select("content").eq("name", name).limit(1).execute()
    )
    return result.data[0]["content"] if result.data else None


def upsert_voice_skill(name: str, content: str) -> None:
    row = {
        "name": name,
        "content": content,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _client().table(TABLE_VOICE_SKILLS).upsert(row, on_conflict="name").execute()
