# Skinstinct Content Bot

Automates drafting LinkedIn posts and email newsletters in Meera Pillai's voice
(`voice_skills/meera_skinstinct.txt` — the single source of truth, never edited
by the pipeline), routes every draft through deterministic + LLM validation,
and delivers it to Telegram for one-tap human approval before publishing.

## 1. System architecture

```
Telegram (/generate, /newsletter)
        │
        ▼
telegram_bot.py ──calls──▶ services/pipeline.py
                                  │
                     ┌────────────┼─────────────┐
                     ▼            ▼             ▼
              prompts.py   gemini_client.py   validator.py
              (system/dev/  (retry+backoff,     (deterministic
               user prompts, JSON parsing)       rule checks)
               voice skill
               injection)
                     │
                     ▼
                 db.py (SQLite: content_items — draft/approved/
                        published/rejected/failed, per voice_skill
                        + channel_id for future multi-brand use)
                     │
                     ▼
           Telegram inline buttons: Approve & publish / Regenerate / Reject
                     │
                     ▼
           telegram_bot.py sends to TELEGRAM_CHANNEL_ID (retried, chunked)
```

Components:
- **`voice_skills/`** — one `.txt` per persona. `ACTIVE_VOICE_SKILL` picks the
  default; `generate_content(voice_skill=...)` can override per call, so
  adding a second brand later is a new file, not a code change.
- **`prompts.py`** — all prompt text (system/developer/user/self-critique/
  improvement). The only file to edit when tuning wording.
- **`services/gemini_client.py`** — the only file that imports `google.genai`.
  Swapping providers means editing this file alone (same seam the sibling
  movie-matchmaker project uses).
- **`services/validator.py`** — deterministic, regex-based implementation of
  the voice skill's own Section 10 self-check. Runs before any LLM judging
  call, so obviously-wrong drafts never cost a second Gemini call.
- **`services/pipeline.py`** — generate → validate → self-critique →
  regenerate-on-failure loop, capped at `MAX_GENERATION_ATTEMPTS`.
- **`db.py`** — SQLite content store / queue / history / audit trail.
- **`services/telegram_bot.py`** — commands, inline-button approval flow,
  publishing, error handling.
- **`main.py`** — wires logging + DB + bot and starts polling.

## 2. Content pipeline

```
Telegram command (category | topic)
  → prompts.user_prompt() injects dynamic vars + recent-post summaries
  → prompts.system_prompt() injects the FULL voice skill file, verbatim, every call
  → gemini_client.generate_json() — structured JSON, retried on 429/5xx
  → services/validator.validate() — hard rules (banned words, emoji, hashtags,
    exclamation marks, markdown structure, number-grounding, narrowing-move,
    non-summary closing, placeholder integrity, format contract per platform)
  → on failure: violations fed back into the next Gemini call as correction
    notes, loop (max N attempts)
  → on deterministic pass: LLM self-critique pass (voice specificity, layered
    mechanism, steelman-before-critique, closing quality) — fuzzy checks a
    regex can't do reliably
  → on failure: issues fed back, loop
  → on pass: saved to db as status="draft", sent to TELEGRAM_ADMIN_CHAT_ID
    with Approve/Regenerate/Reject buttons
  → Approve → services/telegram_bot._publish_to_channel() (retried) → status="published"
```

If `MAX_GENERATION_ATTEMPTS` is exhausted, the best-effort draft is still saved
(status=`failed`) and delivered to Telegram clearly marked **NEEDS HUMAN
REVIEW** rather than silently discarded or silently published.

## 3. Gemini integration

- The entire voice skill file is injected as the **system instruction** on
  every single call — never summarized, never cached-and-reused-stale. This is
  the most token-expensive part of the design and it's deliberate: paraphrasing
  a voice this specific to save tokens is exactly what breaks it.
- The **developer prompt** (`prompts.DEVELOPER_PROMPT`) is separate from the
  persona and only describes the JSON output contract — this keeps persona
  tuning and pipeline-contract tuning independent of each other.
- **Context/memory strategy**: `db.recent_summaries()` pulls the opening line
  of the last 5 published pieces per platform and injects them as "don't repeat
  these" context — cheap (a few lines, not full past posts) and directly
  targets the failure mode of an automated voice repeating its own tricks.
- **Token optimization**: `response_mime_type="application/json"` nudges
  compact structured output instead of prose-wrapped JSON; correction-loop
  prompts only ever include the specific violations, not the whole persona
  re-explained.
- **Hallucination minimization**: `real_data` (verified figures the caller
  supplies) is the only source of truth for numbers; when absent, the model is
  explicitly told every number must be a bracketed placeholder, and
  `validator.py`'s `UNBRACKETED_STAT_PATTERN` check catches a fabricated pH/%
  value that slips through anyway.
- **Few-shot examples**: deliberately not hardcoded. The voice skill's own
  example sentences are marked "paraphrased, not to be copied verbatim," and
  inventing fake "past Meera posts" as few-shot material would risk baking in
  fabricated quotes attributed to a real person. If you have real past
  LinkedIn/newsletter posts, drop them in `voice_skills/examples/` and wire
  them into `prompts.user_prompt()` — the seam is there, just not populated.

## 4. Telegram integration

- Commands: `/start`, `/generate <category> | <topic>` (LinkedIn),
  `/newsletter <category> | <topic>`, `/status` (pending drafts), `/history`.
- Every draft ships with inline buttons: **Approve & publish**, **Regenerate**,
  **Reject** — nothing reaches `TELEGRAM_CHANNEL_ID` without a human tap.
- **Error handling**: a global `error_handler` catches unhandled exceptions,
  logs them, and pings `TELEGRAM_ADMIN_CHAT_ID` so failures aren't silent.
- **Retry logic**: publishing is wrapped in `tenacity` exponential backoff
  (3 attempts); Gemini calls have their own separate retry (4 attempts) scoped
  to transient errors only (429/5xx), never to validation failures.
- **Status/logging**: `/status` and `/history` read directly from `db.py`;
  every request/response/retry is logged via `logging_config.py` (rotating
  file + console), with a filter that redacts any log line that looks like it
  contains a raw secret.
- Long content is chunked under Telegram's 4096-character message limit.

## 5. Prompt engineering — where each piece lives

| Deliverable | Location |
|---|---|
| System prompt | `prompts.system_prompt()` — the voice skill file, verbatim |
| Developer prompt | `prompts.DEVELOPER_PROMPT` — JSON output contract |
| User prompt template | `prompts.user_prompt()` |
| Dynamic variables | `category`, `platform`, `topic`, `real_data` |
| Context injection | `recent_summaries` param, sourced from `db.recent_summaries()` |
| Memory strategy | last 5 opening lines per platform, not full text (token-cheap) |
| Few-shot examples | seam provided, intentionally unpopulated (see §3) |
| Output formatting rules | end of `DEVELOPER_PROMPT` |
| Quality control prompt | `prompts.self_critique_prompt()` |
| Self-critique prompt | same — LLM-as-judge, JSON verdict |
| Final improvement prompt | `prompts.improvement_prompt()` (available for a manual "fix this specific thing" pass; the automatic loop currently re-runs the user prompt with correction notes rather than this, since that reuses the persona instructions naturally — swap in `improvement_prompt()` in `pipeline.py` if you want surgical single-paragraph edits instead of full regenerations) |

## 6. Validation layer

Implemented as code, not a prompt, wherever a regex can check it reliably —
see `services/validator.py`. Checklist from the voice skill's own Section 10:

| Check | Enforced |
|---|---|
| Opens cold, no throat-clearing | ✓ (LinkedIn: no greeting) |
| At least one real number/date/range | ✓ |
| At least one narrowing disclaimer | ✓ |
| Own product framed as documentation, not pitch | not automatable by regex — covered by self-critique's `sounds_like_specific_person` + human approval step |
| Zero banned vocabulary | ✓ |
| Zero emoji/hashtags/exclamation marks | ✓ |
| No bullets/subheadings/bold in body | ✓ |
| Closing is action/pointer, not summary | ✓ |
| Every statistic real or placeholder | ✓ (best-effort regex; real/fake ultimately depends on `real_data` being honest) |
| Sounds like a specific person, not a brand | LLM self-critique pass |

Failing either layer triggers automatic regeneration with the specific
violations fed back to Gemini, up to `MAX_GENERATION_ATTEMPTS`.

## 7. Error handling

| Failure | Handling |
|---|---|
| Gemini 429/5xx/timeout | `tenacity` retry, exponential backoff + jitter, 4 attempts |
| Gemini returns non-JSON | Caught, correction prompt sent, counts as a pipeline attempt |
| Gemini returns empty response | `GeminiEmptyResponseError`, surfaced to the Telegram command as a failure message |
| Validation fails repeatedly | Best draft saved as `status=failed`, delivered flagged NEEDS HUMAN REVIEW instead of lost |
| Telegram send fails | `tenacity` retry (3 attempts); failure surfaced back to the approver, not swallowed |
| Any unhandled exception | Global `error_handler` logs + notifies `TELEGRAM_ADMIN_CHAT_ID` |

## 8. Scalability — what's already schema-ready

- **Multiple voice skills**: `voice_skills/*.txt` + `voice_skill` column on
  every content item; `generate_content(voice_skill=...)`.
- **Multiple channels/brands**: `channel_id` column already threaded through
  `db.py` and `pipeline.py`; today only one `TELEGRAM_CHANNEL_ID` env var is
  read, but the data model doesn't need to change to support more.
- **Scheduled publishing / content queues**: `status` column already models a
  queue (`draft → approved → published`); adding a scheduler means a small
  script that polls `db.pending_drafts()`/`approved` items and calls the
  existing `_publish_to_channel` — no schema change.
- **Analytics**: `content_items` already has timestamps, attempt counts, and
  violation history per item — a dashboard is a read-only query away.
- **Version control**: the voice skill file is a plain text file — put this
  whole project under git (`git init`) and every prompt/persona change is
  diffable.
- **Future API providers**: `services/gemini_client.py` is the only file that
  imports `google.genai`; `pipeline.py` only calls `.generate_json(...)`.

## Setup

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env`:
- `GEMINI_API_KEY` — aistudio.google.com/apikey
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `TELEGRAM_ADMIN_CHAT_ID` — your own chat id (message @userinfobot to get it)
- `TELEGRAM_CHANNEL_ID` — leave blank until you're ready to publish live; the
  approval flow still works without it, it just can't complete the publish step

Run the tests (no API keys required — they use a fake Gemini client):

```bash
venv\Scripts\python -m unittest discover -s tests -v
```

Run the bot:

```bash
venv\Scripts\python main.py
```

Then in Telegram: `/generate ingredient | niacinamide flushing at high concentration`.

## Known limitations / next steps

- Polling mode (`run_polling`), not webhooks — fine for one bot, switch to a
  webhook behind a real WSGI/ASGI server if this needs to scale past one
  admin's laptop staying on.
- The self-critique pass is a second Gemini call per attempt — real cost/
  quality tradeoff; if Gemini cost becomes a concern, gate it behind the
  deterministic pass only for low-stakes categories.
- No scheduler yet (see §8) — publishing today is always human-triggered via
  the Approve button.
