from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


SERVICES_BUTTON = "🔧 Services & prices"
VEHICLES_BUTTON = "🚗 My vehicles"
BOOKING_BUTTON = "📅 Book a service"
APPOINTMENTS_BUTTON = "📋 My appointments"

MENU_LABELS = (
    "🤖 Describe a problem",
    BOOKING_BUTTON,
    SERVICES_BUTTON,
    VEHICLES_BUTTON,
    APPOINTMENTS_BUTTON,
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label)] for label in MENU_LABELS],
        resize_keyboard=True,
    )
