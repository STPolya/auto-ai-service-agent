from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def choices_keyboard(token: str, action: str, choices: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label[:100], callback_data=f"book:{token}:{action}:{identifier}"),
    ] for identifier, label in choices])


def confirmation_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Confirm", callback_data=f"book:{token}:confirm:0"),
        InlineKeyboardButton(text="❌ Cancel", callback_data=f"book:{token}:cancel:0"),
    ]])
