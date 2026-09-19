"""Load local configuration without overriding environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv


def get_bot_token() -> str:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN is required. Set it in your environment "
            "or copy .env.example to .env and fill in your bot token."
        )
    return token
