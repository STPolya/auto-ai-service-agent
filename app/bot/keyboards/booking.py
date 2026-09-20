from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def choices_keyboard(token: str, action: str, choices: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label[:100], callback_data=f"book:{token}:{action}:{identifier}"),
    ] for identifier, label in choices])


def confirmation_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"book:{token}:confirm:0"),
        InlineKeyboardButton(text="❌ Отменить", callback_data=f"book:{token}:cancel:0"),
    ]])


def times_keyboard(token: str, times: list[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=value, callback_data=
               f"book:{token}:time:{int(value[:2]) * 60 + int(value[3:])}") for value in times]
    rows = [buttons[index:index + 3] for index in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="📅 Другая дата", callback_data=f"book:{token}:date:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
