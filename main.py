"""Local-development entrypoint: Telegram polling instead of webhooks, so you
don't need a public URL to test against. Talks to the same Supabase database
as the Vercel deployment (api/index.py) — never run both against the same bot
token at once, Telegram only delivers each update to one of them."""
import logging

import db
from config import settings
from logging_config import configure_logging
from services.telegram_bot import build_application

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    db.init_db()
    logger.info(
        "Starting bot — voice_skill=%s gemini_model=%s",
        settings.active_voice_skill, settings.gemini_model,
    )
    app = build_application()
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
