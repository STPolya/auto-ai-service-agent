from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

ADD_VEHICLE_CALLBACK = "vehicle:add"


def add_vehicle_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Add vehicle", callback_data=ADD_VEHICLE_CALLBACK),
    ]])
