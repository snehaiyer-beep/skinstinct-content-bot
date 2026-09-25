"""
Two entry points:

- generate_content(): the original structured flow (/generate category|topic).
- generate_from_note(): score a free-form note first: below 6/10, stop and
  tell the human why, no draft made. 6 or above, look for a relevant news
  angle, then draft.

Both share the same generate -> validate -> LLM self-critique ->
regenerate-on-failure loop, up to config.settings.max_generation_attempts.
This is the only place that decides whether a draft is good enough to reach
a human for approval — nothing downstream (telegram_bot.py) re-implements
this logic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import db
import prompts
from services import news, validator
from services.gemini_client import GeminiClient, GeminiJsonParseError

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    passed: bool
    platform: str
    topic: str
    category: str
    subject_line: str | None
    body: str
    placeholders_used: list[str]
    sources_cited: list[str]
    violations: list[str]
    attempts: int
    content_id: int | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class NotePipelineResult:
    accepted: bool  # False means: rejected at the scoring gate, no draft made
    score: int
    score_reason: str
    note_id: int | None
    passed_validation: bool = False
    platform: str = "linkedin"
    subject_line: str | None = None
    body: str = ""
    used_news_item: bool = False
    news_item: dict | None = None
    violations: list[str] = field(default_factory=list)
    attempts: int = 0
    content_id: int | None = None
    warnings: list[str] = field(default_factory=list)


def _call_gemini_for_draft(
    client: GeminiClient,
    *,
    voice_skill_name: str,
    category: str,
    platform: str,
    topic: str,
    real_data: dict | None,
    recent_summaries: list[str],
    news_item: dict | None,
    correction_notes: list[str] | None,
) -> dict:
    system = prompts.system_prompt(voice_skill_name)
    base_user = prompts.user_prompt(
        category=category, platform=platform, topic=topic,
        real_data=real_data, recent_summaries=recent_summaries, news_item=news_item,
    )
    contents = f"{prompts.DEVELOPER_PROMPT}\n\n{base_user}"
    if correction_notes:
        notes = "\n".join(f"- {n}" for n in correction_notes)
        contents += (
            "\n\nYour previous attempt failed these checks — fix them this time:\n" + notes
        )
    return client.generate_json(system_instruction=system, contents=contents)


def _validate_and_critique(
    client: GeminiClient, *, voice_skill_name: str, platform: str, body: str,
    subject_line: str | None, allow_unbracketed_numbers: bool,
) -> tuple[validator.ValidationResult, dict | None]:
    det_result = validator.validate(
        platform=platform, body=body, subject_line=subject_line,
        allow_unbracketed_numbers=allow_unbracketed_numbers,
    )
    if not det_result.passed:
        return det_result, None

    critique = client.generate_json(
        system_instruction=prompts.system_prompt(voice_skill_name),
        contents=prompts.self_critique_prompt(platform=platform, body=body),
    )
    return det_result, critique


def generate_content(
    *,
    category: str,
    platform: str,
    topic: str,
    real_data: dict | None = None,
    voice_skill: str | None = None,
    channel_id: str | None = None,
) -> PipelineResult:
    from config import settings  # local import keeps module import-safe for tests

    voice_skill_name = voice_skill or settings.active_voice_skill
    client = GeminiClient()
    recent = db.recent_summaries(voice_skill_name, platform)

    correction_notes: list[str] | None = None
    last_parsed: dict = {}
    last_violations: list[str] = []
    attempts_used = 0

    for attempt in range(1, settings.max_generation_attempts + 1):
        attempts_used = attempt
        try:
            parsed = _call_gemini_for_draft(
                client, voice_skill_name=voice_skill_name, category=category, platform=platform,
                topic=topic, real_data=real_data, recent_summaries=recent, news_item=None,
                correction_notes=correction_notes,
            )
        except GeminiJsonParseError:
            logger.warning("Attempt %d: Gemini did not return parseable JSON", attempt)
            correction_notes = ["Your last response was not a single valid JSON object. "
                                 "Return ONLY the JSON object, no other text."]
            continue

        last_parsed = parsed
        body = parsed.get("body", "") or ""
        subject_line = parsed.get("subject_line")

        det_result, critique = _validate_and_critique(
            client, voice_skill_name=voice_skill_name, platform=platform, body=body,
            subject_line=subject_line, allow_unbracketed_numbers=bool(real_data),
        )
        if not det_result.passed:
            last_violations = det_result.violations
            correction_notes = det_result.violations
            logger.info("Attempt %d failed deterministic validation: %s", attempt, det_result.violations)
            continue
        if critique.get("verdict") != "pass":
            last_violations = critique.get("issues", []) or ["Self-critique flagged issues with no detail."]
            correction_notes = last_violations
            logger.info("Attempt %d failed self-critique: %s", attempt, last_violations)
            continue

        content_id = db.save_draft(
            voice_skill=voice_skill_name, channel_id=channel_id, category=category,
            platform=platform, topic=topic, subject_line=subject_line, body=body,
            placeholders=parsed.get("placeholders_used", []),
            sources=parsed.get("sources_cited", []), violations=[], attempts=attempt,
            status="pending",
        )
        return PipelineResult(
            passed=True, platform=platform, topic=topic, category=category,
            subject_line=subject_line, body=body,
            placeholders_used=parsed.get("placeholders_used", []),
            sources_cited=parsed.get("sources_cited", []),
            violations=[], attempts=attempt, content_id=content_id,
            warnings=det_result.warnings,
        )

    content_id = db.save_draft(
        voice_skill=voice_skill_name, channel_id=channel_id, category=category,
        platform=platform, topic=topic, subject_line=last_parsed.get("subject_line"),
        body=last_parsed.get("body", ""), placeholders=last_parsed.get("placeholders_used", []),
        sources=last_parsed.get("sources_cited", []), violations=last_violations,
        attempts=attempts_used, status="failed",
    )
    return PipelineResult(
        passed=False, platform=platform, topic=topic, category=category,
        subject_line=last_parsed.get("subject_line"), body=last_parsed.get("body", ""),
        placeholders_used=last_parsed.get("placeholders_used", []),
        sources_cited=last_parsed.get("sources_cited", []),
        violations=last_violations, attempts=attempts_used, content_id=content_id,
    )


def generate_from_note(
    note_text: str,
    *,
    platform: str = "linkedin",
    category: str = "note",
    voice_skill: str | None = None,
    channel_id: str | None = None,
) -> NotePipelineResult:
    from config import settings  # local import keeps module import-safe for tests

    voice_skill_name = voice_skill or settings.active_voice_skill
    client = GeminiClient()

    score_raw = client.generate_json(
        system_instruction=prompts.NOTE_SCORING_SYSTEM_PROMPT,
        contents=prompts.score_note_prompt(note_text),
    )
    score = int(score_raw.get("score", 0))
    reason = score_raw.get("reason", "")

    note_id = db.save_note(voice_skill=voice_skill_name, text=note_text)

    if score < 6:
        db.update_note_score(note_id, score=score, reason=reason, status="scored_reject")
        logger.info("Note %s scored %d/10 (rejected): %s", note_id, score, reason)
        return NotePipelineResult(accepted=False, score=score, score_reason=reason, note_id=note_id)

    db.update_note_score(note_id, score=score, reason=reason, status="scored_pass")

    # News angle lookup is best-effort: never block drafting if it fails.
    news_item: dict | None = None
    search_phrase: str | None = None
    try:
        keyword_raw = client.generate_json(
            system_instruction=prompts.KEYWORD_EXTRACTION_SYSTEM_PROMPT,
            contents=prompts.keyword_extraction_prompt(note_text),
        )
        search_phrase = keyword_raw.get("search_phrase")
        if search_phrase:
            news_item = news.fetch_top_news(search_phrase)
    except Exception:  # noqa: BLE001
        logger.exception("Keyword extraction / news lookup failed, drafting without a news angle")

    recent = db.recent_summaries(voice_skill_name, platform)
    real_data = {"note": note_text}

    correction_notes: list[str] | None = None
    last_parsed: dict = {}
    last_violations: list[str] = []
    attempts_used = 0

    for attempt in range(1, settings.max_generation_attempts + 1):
        attempts_used = attempt
        try:
            parsed = _call_gemini_for_draft(
                client, voice_skill_name=voice_skill_name, category=category, platform=platform,
                topic=note_text, real_data=real_data, recent_summaries=recent,
                news_item=news_item, correction_notes=correction_notes,
            )
        except GeminiJsonParseError:
            logger.warning("Attempt %d: Gemini did not return parseable JSON", attempt)
            correction_notes = ["Your last response was not a single valid JSON object. "
                                 "Return ONLY the JSON object, no other text."]
            continue

        last_parsed = parsed
        body = parsed.get("body", "") or ""
        subject_line = parsed.get("subject_line")

        det_result, critique = _validate_and_critique(
            client, voice_skill_name=voice_skill_name, platform=platform, body=body,
            subject_line=subject_line, allow_unbracketed_numbers=True,
        )
        if not det_result.passed:
            last_violations = det_result.violations
            correction_notes = det_result.violations
            continue
        if critique.get("verdict") != "pass":
            last_violations = critique.get("issues", []) or ["Self-critique flagged issues with no detail."]
            correction_notes = last_violations
            continue

        used_news_item = bool(parsed.get("used_news_item")) and news_item is not None
        final_body = body.rstrip() + prompts.news_verify_block(news_item) if used_news_item else body

        content_id = db.save_draft(
            voice_skill=voice_skill_name, channel_id=channel_id, category=category,
            platform=platform, topic=note_text, subject_line=subject_line, body=final_body,
            placeholders=parsed.get("placeholders_used", []), sources=parsed.get("sources_cited", []),
            violations=[], attempts=attempt, note_id=note_id, search_phrase=search_phrase,
            used_news_item=used_news_item, news_item=news_item if used_news_item else None,
            status="pending",
        )
        return NotePipelineResult(
            accepted=True, score=score, score_reason=reason, note_id=note_id,
            passed_validation=True, platform=platform, subject_line=subject_line,
            body=final_body, used_news_item=used_news_item,
            news_item=news_item if used_news_item else None,
            violations=[], attempts=attempt, content_id=content_id, warnings=det_result.warnings,
        )

    content_id = db.save_draft(
        voice_skill=voice_skill_name, channel_id=channel_id, category=category,
        platform=platform, topic=note_text, subject_line=last_parsed.get("subject_line"),
        body=last_parsed.get("body", ""), placeholders=last_parsed.get("placeholders_used", []),
        sources=last_parsed.get("sources_cited", []), violations=last_violations,
        attempts=attempts_used, note_id=note_id, search_phrase=search_phrase,
        used_news_item=False, status="failed",
    )
    return NotePipelineResult(
        accepted=True, score=score, score_reason=reason, note_id=note_id,
        passed_validation=False, platform=platform, subject_line=last_parsed.get("subject_line"),
        body=last_parsed.get("body", ""), violations=last_violations, attempts=attempts_used,
        content_id=content_id,
    )
