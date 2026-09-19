from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


MENU_LABELS = (
    "🤖 Describe a problem",
    "📅 Book a service",
    "🔧 Services & prices",
    "📋 My appointments",
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label)] for label in MENU_LABELS],
        resize_keyboard=True,
    )
