"""Environment-driven configuration. No secret is ever logged."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
VOICE_SKILLS_DIR = BASE_DIR / "voice_skills"
LOGS_DIR = BASE_DIR / "logs"


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"))
    gemini_temperature: float = field(
        default_factory=lambda: float(os.environ.get("GEMINI_TEMPERATURE", "0.85"))
    )

    telegram_bot_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    telegram_admin_chat_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_ADMIN_CHAT_ID", ""))
    telegram_channel_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_CHANNEL_ID", ""))
    telegram_webhook_secret: str = field(
        default_factory=lambda: os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    )

    supabase_url: str = field(default_factory=lambda: os.environ.get("SUPABASE_URL", ""))
    supabase_service_key: str = field(default_factory=lambda: os.environ.get("SUPABASE_SERVICE_KEY", ""))

    public_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_URL", ""))

    active_voice_skill: str = field(
        default_factory=lambda: os.environ.get("ACTIVE_VOICE_SKILL", "meera_skinstinct")
    )
    max_generation_attempts: int = field(
        default_factory=lambda: int(os.environ.get("MAX_GENERATION_ATTEMPTS", "3"))
    )
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))

    def voice_skill_path(self, name: str | None = None) -> Path:
        return VOICE_SKILLS_DIR / f"{name or self.active_voice_skill}.txt"

    def require_gemini(self) -> None:
        if not self.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to .env before generating content."
            )

    def require_telegram(self) -> None:
        missing = [
            name
            for name, val in (
                ("TELEGRAM_BOT_TOKEN", self.telegram_bot_token),
                ("TELEGRAM_ADMIN_CHAT_ID", self.telegram_admin_chat_id),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(f"Missing required Telegram settings: {', '.join(missing)}")

    def require_supabase(self) -> None:
        missing = [
            name
            for name, val in (
                ("SUPABASE_URL", self.supabase_url),
                ("SUPABASE_SERVICE_KEY", self.supabase_service_key),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(f"Missing required Supabase settings: {', '.join(missing)}")


settings = Settings()
