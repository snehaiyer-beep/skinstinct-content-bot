"""
Vercel entrypoint. Telegram calls this over HTTPS (webhook mode) instead of
the bot polling Telegram for updates — required because Vercel functions only
run in response to a request and don't stay alive in the background.

Each request builds a fresh python-telegram-bot Application, feeds it the one
update it received, and tears it down. That's the standard pattern for
running PTB in a serverless function (see main.py for the alternative:
long-running polling, used for local development).
"""
from __future__ import annotations

import asyncio
import logging

from flask import Flask, jsonify, request
from telegram import Update

from config import settings
from logging_config import configure_logging
from services.telegram_bot import build_application

configure_logging()
logger = logging.getLogger(__name__)

app = Flask(__name__)


async def _process_update(update_data: dict) -> None:
    application = build_application()
    await application.initialize()
    try:
        update = Update.de_json(update_data, application.bot)
        await application.process_update(update)
    finally:
        await application.shutdown()


@app.post("/api/webhook")
def webhook():
    if settings.telegram_webhook_secret:
        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if header != settings.telegram_webhook_secret:
            logger.warning("Rejected webhook request with missing/invalid secret token")
            return jsonify({"ok": False}), 401

    update_data = request.get_json(force=True, silent=True)
    if not update_data:
        return jsonify({"ok": False, "error": "no update body"}), 400

    try:
        asyncio.run(_process_update(update_data))
    except Exception:  # noqa: BLE001 — never let Telegram retry-storm a broken update
        logger.exception("Failed to process update")
        return jsonify({"ok": False}), 200

    return jsonify({"ok": True})


@app.get("/api/webhook")
def webhook_probe():
    return jsonify({"status": "webhook endpoint is up, use POST for Telegram"})


@app.get("/")
def index():
    return jsonify({"service": "skinstinct-content-bot", "status": "ok"})
