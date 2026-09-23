"""Logging setup: rotating file handler + console. Never logs secret values."""
import logging
from logging.handlers import RotatingFileHandler

from config import LOGS_DIR, settings

_SECRET_MARKERS = ("API_KEY", "BOT_TOKEN", "SECRET")


class RedactSecretsFilter(logging.Filter):
    """Defense in depth: drops a record if it looks like a raw secret leaked into a log call."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        if any(marker in msg and "=" in msg for marker in _SECRET_MARKERS):
            record.msg = "[redacted log line — appeared to contain a secret value]"
            record.args = ()
        return True


def configure_logging() -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    root = logging.getLogger()
    root.setLevel(settings.log_level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = RotatingFileHandler(
        LOGS_DIR / "bot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(RedactSecretsFilter())

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.addFilter(RedactSecretsFilter())

    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
