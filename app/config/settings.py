"""Load local configuration without overriding environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv


def _required_environment(name: str) -> str:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(
            f"{name} is required. Set it in your environment or local .env file."
        )
    return value


def get_bot_token() -> str:
    return _required_environment("TELEGRAM_BOT_TOKEN")


def get_database_url() -> str:
    """Require database configuration only when database functionality is used."""
    return _required_environment("DATABASE_URL")


def get_gemini_api_key() -> str:
    """Require the Gemini key only when diagnostics are requested."""
    return _required_environment("GEMINI_API_KEY")


def get_admin_api_key() -> str:
    """Require admin configuration only for protected HTTP requests."""
    key = _required_environment("ADMIN_API_KEY")
    if key == "replace_with_a_strong_random_value":
        raise ValueError("ADMIN_API_KEY must not use the example placeholder.")
    return key
