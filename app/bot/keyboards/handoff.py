"""Conversation-bound inline handoff; stale buttons cannot hand off a new chat."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

HANDOFF_BUTTON = "👨‍💼 Связаться с оператором"
HANDOFF_PREFIX = "handoff:"


def handoff_keyboard(conversation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=HANDOFF_BUTTON, callback_data=f"{HANDOFF_PREFIX}{conversation_id}"),
    ]])
