"""
Telegram bot: commands to generate drafts, inline-button approval flow, and
publishing to the target channel. All Gemini calls are synchronous SDK calls,
so they're pushed onto a thread via asyncio.to_thread to avoid blocking
python-telegram-bot's event loop.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

import db
from config import settings
from services import pipeline

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096


def _format_draft_message(result: pipeline.PipelineResult) -> str:
    header = f"Draft #{result.content_id} — {result.platform} — {result.category}\n"
    header += f"Topic: {result.topic}\n"
    if result.subject_line:
        header += f"Subject: {result.subject_line}\n"
    header += f"Attempts: {result.attempts}\n"
    if result.placeholders_used:
        header += f"Placeholders needing real data: {len(result.placeholders_used)}\n"
    if result.warnings:
        header += f"Warnings: {'; '.join(result.warnings)}\n"
    header += "\n---\n\n"
    return header + result.body


def _approval_keyboard(content_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Approve & publish", callback_data=f"approve:{content_id}"),
                InlineKeyboardButton("Regenerate", callback_data=f"regen:{content_id}"),
            ],
            [InlineKeyboardButton("Reject", callback_data=f"reject:{content_id}")],
        ]
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Skinstinct content bot.\n\n"
        "/generate <category> | <topic> — draft a LinkedIn post\n"
        "/newsletter <category> | <topic> — draft a newsletter\n"
        "/status — pending drafts\n"
        "/history — recent items\n\n"
        "Categories: ingredient, founder_story, india_context, industry_transparency, "
        "formulation_science, brand_philosophy, consumer_education"
    )


def _parse_category_topic(args: list[str]) -> tuple[str, str] | None:
    if not args:
        return None
    joined = " ".join(args)
    if "|" in joined:
        category, topic = joined.split("|", 1)
        return category.strip(), topic.strip()
    return joined.strip(), joined.strip()


async def _run_generation(update: Update, platform: str, args: list[str]) -> None:
    parsed = _parse_category_topic(args)
    if not parsed:
        await update.message.reply_text(
            "Usage: /generate <category> | <topic>\n"
            "Example: /generate ingredient | niacinamide flushing at high concentration"
        )
        return

    category, topic = parsed
    status_msg = await update.message.reply_text(f"Generating {platform} draft on: {topic} ...")

    try:
        result = await asyncio.to_thread(
            pipeline.generate_content, category=category, platform=platform, topic=topic,
            channel_id=settings.telegram_channel_id or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Generation failed")
        await status_msg.edit_text(f"Generation failed: {exc}")
        return

    text = _format_draft_message(result)
    if not result.passed:
        text = "NEEDS HUMAN REVIEW (failed validation after max attempts)\n\n" + text

    for i in range(0, len(text), TELEGRAM_MESSAGE_LIMIT):
        chunk = text[i : i + TELEGRAM_MESSAGE_LIMIT]
        is_last = i + TELEGRAM_MESSAGE_LIMIT >= len(text)
        await update.message.reply_text(
            chunk, reply_markup=_approval_keyboard(result.content_id) if is_last else None
        )
    await status_msg.delete()


async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_generation(update, "linkedin", context.args)


async def cmd_newsletter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_generation(update, "newsletter", context.args)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    drafts = db.pending_drafts()
    if not drafts:
        await update.message.reply_text("No pending drafts.")
        return
    lines = [f"#{d['id']} [{d['platform']}] {d['topic']} ({d['created_at']})" for d in drafts]
    await update.message.reply_text("\n".join(lines))


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = db.recent_items(10)
    if not items:
        await update.message.reply_text("No content generated yet.")
        return
    lines = [f"#{i['id']} [{i['status']}] [{i['platform']}] {i['topic']}" for i in items]
    await update.message.reply_text("\n".join(lines))


@retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=1, max=10), reraise=True)
async def _publish_to_channel(app: Application, body: str, subject_line: str | None) -> str:
    text = (f"{subject_line}\n\n{body}" if subject_line else body)[:TELEGRAM_MESSAGE_LIMIT]
    try:
        msg = await app.bot.send_message(chat_id=settings.telegram_channel_id, text=text)
    except TelegramError as exc:
        logger.warning("Telegram publish failed, will retry: %s", exc)
        raise
    return str(msg.message_id)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, _, id_str = query.data.partition(":")
    content_id = int(id_str)
    item = db.get_item(content_id)
    if item is None:
        await query.edit_message_text("This draft no longer exists.")
        return

    if action == "approve":
        if not settings.telegram_channel_id:
            await query.edit_message_text(
                "TELEGRAM_CHANNEL_ID is not configured — cannot publish. "
                "Approved in the database; publish manually or set the channel and retry."
            )
            db.update_status(content_id, "approved")
            return
        try:
            message_id = await _publish_to_channel(context.application, item["body"], item["subject_line"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("Publish failed after retries")
            await query.edit_message_text(f"Publish failed after retries: {exc}")
            return
        db.update_status(content_id, "published", telegram_message_id=message_id)
        await query.edit_message_text(query.message.text + "\n\n[PUBLISHED]")

    elif action == "regen":
        await query.edit_message_text(query.message.text + "\n\n[Regenerating...]")
        try:
            result = await asyncio.to_thread(
                pipeline.generate_content, category=item["category"], platform=item["platform"],
                topic=item["topic"], channel_id=item["channel_id"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Regeneration failed")
            await query.message.reply_text(f"Regeneration failed: {exc}")
            return
        db.update_status(content_id, "rejected")
        text = _format_draft_message(result)
        await query.message.reply_text(text, reply_markup=_approval_keyboard(result.content_id))

    elif action == "reject":
        db.update_status(content_id, "rejected")
        await query.edit_message_text(query.message.text + "\n\n[REJECTED]")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled error while processing update: %s", context.error, exc_info=context.error)
    if settings.telegram_admin_chat_id:
        try:
            await context.bot.send_message(
                chat_id=settings.telegram_admin_chat_id,
                text=f"Bot error: {context.error}",
            )
        except TelegramError:
            logger.exception("Failed to notify admin chat of error")


def build_application() -> Application:
    settings.require_telegram()
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("generate", cmd_generate))
    app.add_handler(CommandHandler("newsletter", cmd_newsletter))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)
    return app
