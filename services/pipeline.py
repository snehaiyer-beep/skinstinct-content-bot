"""
Orchestrates: prompt build -> Gemini generate -> deterministic validation ->
LLM self-critique -> regenerate-on-failure, up to config.settings.max_generation_attempts.

This is the only place that decides whether a draft is good enough to reach a
human for approval. Nothing downstream (telegram_bot.py) re-implements this
logic — it just trusts PipelineResult.passed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import db
import prompts
from services import validator
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


def _call_gemini_for_draft(
    client: GeminiClient,
    *,
    category: str,
    platform: str,
    topic: str,
    real_data: dict | None,
    recent_summaries: list[str],
    correction_notes: list[str] | None,
) -> dict:
    system = prompts.system_prompt()
    base_user = prompts.user_prompt(
        category=category, platform=platform, topic=topic,
        real_data=real_data, recent_summaries=recent_summaries,
    )
    contents = f"{prompts.DEVELOPER_PROMPT}\n\n{base_user}"
    if correction_notes:
        notes = "\n".join(f"- {n}" for n in correction_notes)
        contents += (
            "\n\nYour previous attempt failed these checks — fix them this time:\n" + notes
        )
    return client.generate_json(system_instruction=system, contents=contents)


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
                client, category=category, platform=platform, topic=topic,
                real_data=real_data, recent_summaries=recent,
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

        det_result = validator.validate(
            platform=platform, body=body, subject_line=subject_line,
            allow_unbracketed_numbers=bool(real_data),
        )

        if not det_result.passed:
            last_violations = det_result.violations
            correction_notes = det_result.violations
            logger.info("Attempt %d failed deterministic validation: %s", attempt, det_result.violations)
            continue

        critique = client.generate_json(
            system_instruction=prompts.system_prompt(voice_skill_name),
            contents=prompts.self_critique_prompt(platform=platform, body=body),
        )
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
            status="draft",
        )
        return PipelineResult(
            passed=True, platform=platform, topic=topic, category=category,
            subject_line=subject_line, body=body,
            placeholders_used=parsed.get("placeholders_used", []),
            sources_cited=parsed.get("sources_cited", []),
            violations=[], attempts=attempt, content_id=content_id,
            warnings=det_result.warnings,
        )

    # exhausted attempts — save the last draft flagged as failed, for human review
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
