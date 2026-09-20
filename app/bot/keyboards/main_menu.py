from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


SERVICES_BUTTON = "🔧 Услуги и цены"
DIAGNOSTICS_BUTTON = "🤖 Описать проблему"
VEHICLES_BUTTON = "🚗 Мои автомобили"
BOOKING_BUTTON = "📅 Записаться на сервис"
APPOINTMENTS_BUTTON = "📋 Мои записи"

MENU_LABELS = (
    DIAGNOSTICS_BUTTON,
    BOOKING_BUTTON,
    SERVICES_BUTTON,
    APPOINTMENTS_BUTTON,
    VEHICLES_BUTTON,
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label)] for label in MENU_LABELS],
        resize_keyboard=True,
    )
