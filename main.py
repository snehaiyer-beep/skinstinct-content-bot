"""Entrypoint — wires up logging, the database, and Telegram polling."""
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
