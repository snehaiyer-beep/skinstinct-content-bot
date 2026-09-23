"""
Registers (or clears) the Telegram webhook so updates get delivered to the
deployed Vercel URL instead of expecting the bot to poll for them.

Usage:
    python scripts/set_webhook.py https://your-app.vercel.app
    python scripts/set_webhook.py --clear
    python scripts/set_webhook.py --info
"""
from __future__ import annotations

import sys

import requests

from config import settings


def _api(method: str, **params) -> dict:
    settings.require_telegram()
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    response = requests.post(url, json=params, timeout=15)
    response.raise_for_status()
    return response.json()


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    arg = sys.argv[1]

    if arg == "--info":
        print(_api("getWebhookInfo"))
        return

    if arg == "--clear":
        print(_api("deleteWebhook", drop_pending_updates=False))
        return

    base_url = arg.rstrip("/")
    webhook_url = f"{base_url}/api/webhook"
    params = {"url": webhook_url}
    if settings.telegram_webhook_secret:
        params["secret_token"] = settings.telegram_webhook_secret
    else:
        print(
            "Warning: TELEGRAM_WEBHOOK_SECRET is not set in .env — the webhook "
            "will accept requests from anyone who finds the URL. Strongly "
            "recommended to set one before going live.",
            file=sys.stderr,
        )

    result = _api("setWebhook", **params)
    print(result)


if __name__ == "__main__":
    main()
