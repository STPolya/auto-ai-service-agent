from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


SERVICES_BUTTON = "🔧 Services & prices"

MENU_LABELS = (
    "🤖 Describe a problem",
    "📅 Book a service",
    SERVICES_BUTTON,
    "📋 My appointments",
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label)] for label in MENU_LABELS],
        resize_keyboard=True,
    )
