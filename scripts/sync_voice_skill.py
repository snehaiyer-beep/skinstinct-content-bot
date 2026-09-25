"""
Seeds/updates a voice skill's content in Supabase from its local file — run
this once after creating the skinstinct_voice_skills table, and again
whenever you hand-edit voice_skills/<name>.txt and want the change live
without a redeploy.

Usage:
    python scripts/sync_voice_skill.py meera_skinstinct
"""
from __future__ import annotations

import sys

import db
from config import settings


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else settings.active_voice_skill
    path = settings.voice_skill_path(name)
    if not path.exists():
        print(f"No local file at {path}")
        sys.exit(1)

    content = path.read_text(encoding="utf-8")
    db.upsert_voice_skill(name, content)
    print(f"Synced {path} ({len(content)} chars) to Supabase as voice skill {name!r}.")


if __name__ == "__main__":
    main()
