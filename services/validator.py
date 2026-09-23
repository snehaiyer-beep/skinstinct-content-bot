"""
Deterministic validation layer — implements the voice skill's own Section 10
self-check as code, plus the hard rules from Section 2 and Section 6. This is
cheap, fast, and 100% reproducible, so it runs before any LLM self-critique
call and before anything is ever shown to a human for approval.

Anything fuzzy (does this sound like a specific person, does each paragraph add
a new mechanism) is deliberately NOT handled here — that's services/pipeline.py's
job via the LLM self-critique prompt. This module only checks things a regex can
check correctly and consistently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

BANNED_PHRASES = [
    "game-changer", "game changer", "journey", "elevate", "unlock",
    "revolutionize", "cutting-edge", "cutting edge", "holy grail", "obsessed",
    "skin-loving", "skin loving", "glow-up", "glow up", "synergy",
    "we're excited to", "we are excited to", "here's the tea", "let's dive in",
    "in today's world of skincare",
]

NARROWING_MOVE_PATTERNS = [
    r"i'?m not saying", r"i am not saying", r"what i'?m saying is",
    r"i want to be precise about", r"i want to be careful",
    r"i'?m not trying to", r"that'?s not what i'?m",
]

SUMMARY_CLOSING_PATTERNS = [
    r"and that'?s why", r"in conclusion", r"to summari[sz]e",
    r"at the end of the day", r"so remember",
]

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\U00002700-\U000027BF"
    "]",
    flags=re.UNICODE,
)

HASHTAG_PATTERN = re.compile(r"(?<!\S)#\w+")
MARKDOWN_HEADER_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s", re.MULTILINE)
MARKDOWN_BULLET_PATTERN = re.compile(r"^\s{0,3}[-*•]\s", re.MULTILINE)
MARKDOWN_NUMBERED_LIST_PATTERN = re.compile(r"^\s{0,3}\d+[.)]\s", re.MULTILINE)
MARKDOWN_BOLD_PATTERN = re.compile(r"\*\*[^*]+\*\*")

NUMBER_GROUNDING_PATTERN = re.compile(
    r"\d+(\.\d+)?\s?%|\bpH\s?\d|\d{4}\b|\b\d+\s?(month|week|year|day)s?\b",
    re.IGNORECASE,
)
PLACEHOLDER_PATTERN = re.compile(r"\[(INSERT|VERIFY SOURCE)[^\]]*\]", re.IGNORECASE)

# a bare number/percent/pH NOT immediately followed by, or inside, a placeholder bracket
UNBRACKETED_STAT_PATTERN = re.compile(
    r"(?<!\[INSERT: )(?<!\[VERIFY SOURCE)(\bpH\s?\d(\.\d+)?(-\d(\.\d+)?)?\b|\b\d+(\.\d+)?%\b)",
    re.IGNORECASE,
)

WORD_COUNT_RANGES = {
    "linkedin": (300, 600),   # soft band around the 380-520 target; hard-fail well outside it
    "newsletter": (400, 900),  # soft band around the 500-750 target
}

GREETING_PATTERN = re.compile(r"^\s*(hi|hello|dear|hey)\b", re.IGNORECASE)


@dataclass
class ValidationResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_feedback_list(self) -> list[str]:
        return self.violations


def _word_count(text: str) -> int:
    return len(text.split())


def validate(
    *,
    platform: str,
    body: str,
    subject_line: str | None,
    allow_unbracketed_numbers: bool,
) -> ValidationResult:
    violations: list[str] = []
    warnings: list[str] = []
    lower = body.lower()

    for phrase in BANNED_PHRASES:
        if phrase in lower:
            violations.append(f'Banned vocabulary used: "{phrase}"')

    if EMOJI_PATTERN.search(body):
        violations.append("Contains an emoji — persona requires zero emojis.")

    if HASHTAG_PATTERN.search(body):
        violations.append("Contains a hashtag — persona requires zero hashtags.")

    if "!" in body:
        violations.append("Contains an exclamation mark — persona requires zero.")

    if MARKDOWN_HEADER_PATTERN.search(body):
        violations.append("Contains a markdown header — body must be continuous prose.")
    if MARKDOWN_BULLET_PATTERN.search(body):
        violations.append("Contains a bullet point — body must be continuous prose.")
    if MARKDOWN_NUMBERED_LIST_PATTERN.search(body):
        violations.append("Contains a numbered list — body must be continuous prose.")
    if MARKDOWN_BOLD_PATTERN.search(body):
        violations.append("Contains bold markdown — body must be continuous prose.")

    if not NUMBER_GROUNDING_PATTERN.search(body):
        violations.append(
            "No specific number, percentage, pH value, or date found — persona "
            "requires at least one concrete figure grounding the claim (or a "
            "bracketed placeholder standing in for one)."
        )

    if not any(re.search(p, lower) for p in NARROWING_MOVE_PATTERNS):
        violations.append(
            'Missing a narrowing disclaimer (e.g. "I\'m not saying X, I\'m saying Y").'
        )

    if any(re.search(p, lower) for p in SUMMARY_CLOSING_PATTERNS):
        violations.append(
            "Closing reads like a moralizing summary — persona requires ending on an "
            "action, a forward pointer, or one flat closing sentence, never a recap."
        )

    if not allow_unbracketed_numbers:
        stray = UNBRACKETED_STAT_PATTERN.findall(body)
        has_placeholder = bool(PLACEHOLDER_PATTERN.search(body))
        if stray and not has_placeholder:
            violations.append(
                "Contains a precise-looking statistic (pH/%% value) with no verified "
                "real_data supplied and no [INSERT:]/[VERIFY SOURCE] placeholder — "
                "this looks like a fabricated figure."
            )

    # Word count is intentionally never a hard failure: the persona explicitly says not
    # to pad to hit a target length, so a short-but-complete piece is correct behavior.
    # Only flag something so far over the band that it looks like the model rambled.
    lo, hi = WORD_COUNT_RANGES.get(platform, (0, 10_000))
    wc = _word_count(body)
    if wc > hi * 1.5:
        violations.append(f"Body is {wc} words, far over the {platform} target — looks padded or rambling.")
    elif wc < lo or wc > hi:
        warnings.append(f"Body is {wc} words, outside the usual {lo}-{hi} band (soft target, not a failure).")

    if platform == "newsletter":
        if not subject_line:
            violations.append("Newsletter is missing a subject line.")
        if not re.match(r"^\s*hi,\s*$", body.splitlines()[0], re.IGNORECASE) if body.splitlines() else True:
            violations.append('Newsletter body must open with "Hi," on its own line.')
        stripped = body.strip()
        if not re.search(r"\bmeera\s*$", stripped, re.IGNORECASE):
            violations.append('Newsletter must sign off with "Meera" alone on the last line.')
    elif platform == "linkedin":
        first_line = body.splitlines()[0] if body.splitlines() else ""
        if GREETING_PATTERN.match(first_line):
            violations.append("LinkedIn post must open cold — no greeting.")
        if subject_line:
            warnings.append("subject_line was set for a LinkedIn post; it will be ignored.")

    return ValidationResult(passed=not violations, violations=violations, warnings=warnings)
