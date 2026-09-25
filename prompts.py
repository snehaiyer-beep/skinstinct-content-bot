"""
All prompt construction lives here — the single place to tune wording without
touching pipeline logic. Nothing here alters the voice skill file itself;
it is always injected verbatim as the system prompt.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache

from config import settings

logger = logging.getLogger(__name__)

PLATFORM_LINKEDIN = "linkedin"
PLATFORM_NEWSLETTER = "newsletter"

OUTPUT_SCHEMA_KEYS = (
    "platform", "subject_line", "body", "placeholders_used", "sources_cited", "used_news_item",
)


@lru_cache(maxsize=8)
def load_voice_skill(name: str | None = None) -> str:
    """Supabase is the primary source (editable without a redeploy) with the local
    file as fallback/seed — see scripts/sync_voice_skill.py. Falls back silently so
    local dev/tests work without Supabase configured."""
    skill_name = name or settings.active_voice_skill
    try:
        from db import get_voice_skill_content

        content = get_voice_skill_content(skill_name)
        if content:
            return content
    except Exception as exc:  # noqa: BLE001 — Supabase optional here, file is the fallback
        logger.debug("Voice skill %r not loaded from Supabase, using local file: %s", skill_name, exc)

    path = settings.voice_skill_path(skill_name)
    if not path.exists():
        raise FileNotFoundError(f"Voice skill file not found: {path}")
    return path.read_text(encoding="utf-8")


def system_prompt(voice_skill_name: str | None = None) -> str:
    """The persona. Injected verbatim, every request — never summarized or paraphrased,
    because paraphrasing is exactly the kind of lossy compression that erodes a voice
    this specific over many generations."""
    return load_voice_skill(voice_skill_name)


DEVELOPER_PROMPT = """\
You are being called by an automated content pipeline, not a chat interface. \
Follow the persona and rules above exactly. In addition, follow this output contract:

Return ONLY a single JSON object — no markdown code fences, no commentary before or \
after it — with exactly these keys:

- "platform": either "linkedin" or "newsletter", echoing what you were asked to write.
- "subject_line": the newsletter subject line (string) if platform is "newsletter", \
otherwise null.
- "body": the full post/newsletter body as one string, using "\\n\\n" between \
paragraphs. For a newsletter this INCLUDES the leading "Hi," line and the trailing \
"Meera" sign-off, per the persona's own format spec. For a LinkedIn post it is body \
text only, opening cold.
- "placeholders_used": an array of every "[INSERT: ...]" or "[VERIFY SOURCE ...]" \
placeholder string you inserted, verbatim, so the pipeline can flag them for human \
follow-up. Empty array if none.
- "sources_cited": an array of any external study/institution citations you named \
in-line (e.g. "University of Liverpool, 2022"), so they can be fact-checked before \
publishing. Empty array if none.
- "used_news_item": boolean. true only if a news item was given to you below AND you \
judged it genuinely relevant AND you actually wove it into the body. false if no news \
item was given, or you judged it a poor fit and ignored it. Never force a connection \
just to set this true.

Do not invent a precise statistic to avoid an empty placeholders_used array — an \
honest placeholder is correct behavior, not a failure.
"""


def user_prompt(
    *,
    category: str,
    platform: str,
    topic: str,
    real_data: dict | None = None,
    recent_summaries: list[str] | None = None,
    news_item: dict | None = None,
) -> str:
    """Dynamic variables + context injection. `real_data` is the caller's own verified
    figures (pH, %, dates, return-rate deltas) — when present, the model is told these
    are the ONLY numbers it may state as fact; anything else must be a placeholder.
    `recent_summaries` is the memory strategy: one line per recent piece, so the model
    avoids repeating an opening scene, a stat, or a rhetorical move back-to-back.
    `news_item` (headline/source/date/summary) is optional context from a live news
    search — the model decides whether it's genuinely relevant; the verify-flag block
    itself is appended by the pipeline afterward, deterministically, never by the model,
    so a hallucinated headline/link can never reach a human under this persona's byline.
    """
    parts = [
        f"Topic category: {category}",
        f"Platform: {platform}",
        f"Specific topic/angle for this piece: {topic}",
    ]

    if real_data:
        parts.append(
            "Verified real data you may cite exactly as given (do not alter precision, "
            "do not round, do not invent additional figures beyond these):\n"
            + json.dumps(real_data, indent=2)
        )
    else:
        parts.append(
            "No verified figures were supplied for this draft. Any number you would "
            "normally state (pH, %, month count, return-rate delta, study citation) "
            "MUST be a bracketed placeholder per Section 7 of the persona — do not "
            "fabricate a plausible-sounding statistic."
        )

    if recent_summaries:
        joined = "\n".join(f"- {s}" for s in recent_summaries)
        parts.append(
            "Recent pieces already published (do not reuse their opening scene, "
            "headline stat, or rhetorical moves — vary the approach):\n" + joined
        )

    if news_item:
        parts.append(
            "A possibly-relevant current news item was found:\n"
            f"Headline: {news_item['headline']}\n"
            f"Source: {news_item['source']}\n"
            f"Date: {news_item['date']}\n"
            f"Summary: {news_item['summary']}\n\n"
            "If this news item is genuinely relevant, use it to make the post timely. "
            "If it doesn't fit naturally, ignore it entirely — do not force a "
            "connection. Set \"used_news_item\" in your JSON response accordingly. Do "
            "not write out a citation block yourself — the pipeline appends the real "
            "source/link automatically when used_news_item is true."
        )

    return "\n\n".join(parts)


NOTE_SCORING_SYSTEM_PROMPT = """\
You are a strict editor screening rough notes before they're turned into a \
LinkedIn post or newsletter. Your only job is judging whether a note has enough \
real substance to build a piece around — not whether it's well written, not \
whether you personally agree with it.

Score 0-10:
- 0-3: a task reminder, a to-do item, a scheduling note, an abandoned sentence \
fragment, or a note with no actual idea, opinion, or detail in it (e.g. "call \
supplier about the shipment", "follow up with Priya", "hmm need to think about \
this more").
- 4-5: names a topic or category but gives no specific angle, detail, or \
opinion on it (e.g. "should write something about sunscreen at some point").
- 6-8: a real observation, opinion, or specific detail with an identifiable \
angle a piece could be built around.
- 9-10: exceptionally rich — concrete numbers, a specific scene, or a sharp, \
well-formed argument already mostly there.

Be strict at the boundary between 5 and 6: if you stripped away the filler \
words and the note would be one bare sentence with no real content, score it \
5 or below, not 6.

Return ONLY a JSON object: {"score": <int 0-10>, "reason": "<one line, plain, \
specific to this note, not generic>"}.
"""


def score_note_prompt(note_text: str) -> str:
    return f"Note to evaluate:\n---\n{note_text}\n---"


KEYWORD_EXTRACTION_SYSTEM_PROMPT = """\
You extract search-engine keywords from a rough content note. You are not \
writing anything — just identifying what a news search should look for to find \
a current, relevant article touching the same topic.
"""


def keyword_extraction_prompt(note_text: str) -> str:
    return f"""\
Read this note and pull 3-5 keywords capturing its core topic, then combine \
them into one short phrase suitable for a news search engine (not a full \
sentence, not quoted).

Note:
---
{note_text}
---

Return ONLY a JSON object: {{"search_phrase": "<short phrase>"}}.
"""


def news_verify_block(news_item: dict) -> str:
    """Built by the pipeline, never by the model — see user_prompt()'s docstring
    for why. Appended to a draft's body only when used_news_item comes back true."""
    bar = "─" * 35
    return (
        f"\n\n{bar}\n"
        f"NEWS SOURCE: {news_item['headline']}\n"
        f"FROM: {news_item['source']} · {news_item['date']}\n"
        f"LINK: {news_item['link']}\n"
        "⚠ Check this before publishing — you are the author of this claim\n"
        f"{bar}"
    )


def self_critique_prompt(*, platform: str, body: str) -> str:
    """LLM-as-judge pass for the things regex can't reliably catch: does this read as
    one specific person thinking, or as generic brand voice; does each layer add a new
    mechanism rather than restating the last one; is a steelman present before the
    critique. Deterministic formatting/banned-word checks are handled separately in
    services/validator.py — this prompt is deliberately narrow so it doesn't duplicate
    that work."""
    return f"""\
You are critiquing a draft {platform} piece against a specific founder's voice \
(persona given in the system prompt — reread it before judging). Do not restate the \
rules; apply them.

Score strictly. Return ONLY a JSON object with keys:
- "sounds_like_specific_person": boolean — false if this could plausibly appear under \
any other founder's byline.
- "layers_add_mechanism": boolean — false if any middle paragraph merely restates an \
earlier point rather than adding a new mechanism/detail.
- "steelman_before_critique": boolean — true only if industry-critique content \
concedes the reasonable version before critiquing it (not required for pieces that \
don't critique anything).
- "closing_is_action_or_pointer": boolean — false if the piece ends on a summary or a \
moralizing wrap-up sentence.
- "issues": array of short strings, each a specific, actionable problem (e.g. \
"paragraph 3 restates paragraph 2's point about pH instead of adding stability data").
- "verdict": "pass" if all four booleans above are true and issues is empty, else \
"revise".

Draft to critique:
---
{body}
---
"""


def improvement_prompt(
    *,
    platform: str,
    body: str,
    subject_line: str | None,
    deterministic_violations: list[str],
    critique_issues: list[str],
) -> str:
    """Final-improvement pass: rewrite addressing named issues only, preserving what
    already worked. Runs after either the deterministic validator or the self-critique
    pass flags problems."""
    violation_lines = "\n".join(f"- {v}" for v in deterministic_violations) or "- (none)"
    issue_lines = "\n".join(f"- {i}" for i in critique_issues) or "- (none)"
    subject_block = f'Subject line: "{subject_line}"\n' if subject_line else ""

    return f"""\
Rewrite the draft below to fix ONLY the specific problems listed. Do not change \
paragraphs that were not flagged. Preserve the concrete details, numbers, and \
placeholders that were already correct.

Hard rule violations to fix:
{violation_lines}

Quality issues to fix:
{issue_lines}

{subject_block}Original draft ({platform}):
---
{body}
---

Return the corrected piece using the same JSON output contract you were given \
earlier (platform, subject_line, body, placeholders_used, sources_cited).
"""


@dataclass(frozen=True)
class PromptBundle:
    system: str
    developer: str
    user: str
