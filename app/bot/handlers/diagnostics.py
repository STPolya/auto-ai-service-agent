"""Telegram interface for persistent diagnostic conversations."""

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

from app.ai.service import DiagnosticError, DiagnosticInputError, MAX_PROBLEM_LENGTH
from app.services.conversation_service import ConversationError, create_conversation, diagnostic_turn
from app.bot.keyboards.main_menu import BOOKING_BUTTON, DIAGNOSTICS_BUTTON, MENU_LABELS, main_menu_keyboard
from app.bot.states.diagnostics import Diagnostics

router = Router(name="diagnostics")
router.message.filter(F.chat.type == "private")
logger = logging.getLogger(__name__)
PROMPT_MESSAGE = (
    "Опишите, что происходит с автомобилем: какие симптомы вы заметили и когда они появляются. "
    "Описание будет отправлено в Gemini для предварительной консультации. "
    "Не отправляйте личные данные. Для отмены — /cancel."
)
ERROR_MESSAGE = "Извините, диагностический помощник временно недоступен. Попробуйте позже."
FOLLOWUP_MESSAGE = f"Если хотите записаться на диагностику, выберите «{BOOKING_BUTTON}»."


@router.message(Command("cancel"), Diagnostics.waiting_for_problem_description)
async def cancel_diagnostics(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Описание проблемы отменено.", reply_markup=main_menu_keyboard())


@router.message(F.text == DIAGNOSTICS_BUTTON)
async def begin_diagnostics(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is None:
        await message.answer(ERROR_MESSAGE)
        return
    try:
        user = message.from_user
        conversation_id = await asyncio.to_thread(create_conversation, user.id, user.username, user.first_name)
    except ConversationError:
        logger.error("Diagnostic conversation creation failed; persistence unavailable.")
        await message.answer(ERROR_MESSAGE, reply_markup=main_menu_keyboard())
        return
    await state.update_data(diagnostic_conversation_id=conversation_id)
    await state.set_state(Diagnostics.waiting_for_problem_description)
    await message.answer(PROMPT_MESSAGE, reply_markup=ReplyKeyboardRemove())


@router.message(Diagnostics.waiting_for_problem_description)
async def receive_problem(message: Message, state: FSMContext) -> None:
    problem = (message.text or "").strip()
    if problem in MENU_LABELS or problem.split(" ", 1)[0].split("@", 1)[0] == "/start":
        await state.clear()
        # Let the existing destination router handle supported navigation.
        raise SkipHandler
    if not problem or len(problem) > MAX_PROBLEM_LENGTH or problem.startswith("/") or problem in MENU_LABELS:
        await message.answer(f"Опишите проблему текстом от 1 до {MAX_PROBLEM_LENGTH} символов или отправьте /cancel.")
        return
    conversation_id = (await state.get_data()).get("diagnostic_conversation_id")
    if message.from_user is None or type(conversation_id) is not int:
        await state.clear()
        await message.answer(ERROR_MESSAGE, reply_markup=main_menu_keyboard())
        return
    try:
        answer = await asyncio.to_thread(diagnostic_turn, message.from_user.id, conversation_id, problem)
    except DiagnosticInputError as error:
        await state.set_state(Diagnostics.waiting_for_problem_description)
        await message.answer(str(error))
        return
    except (DiagnosticError, ConversationError):
        # Retain the active conversation after failure; never store a UI error as advice.
        logger.error("Diagnostic request failed; provider or persistence unavailable.")
        await message.answer(ERROR_MESSAGE, reply_markup=main_menu_keyboard())
        return
    # Bound UTF-16 length conservatively; send plain text, never model-generated markup.
    for offset in range(0, len(answer), 2000):
        await message.answer(answer[offset:offset + 2000], parse_mode=None)
    await message.answer(FOLLOWUP_MESSAGE, reply_markup=main_menu_keyboard())
