"""Single-message diagnostic input; no chat history is stored."""

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

from app.ai.service import DiagnosticError, DiagnosticInputError, MAX_PROBLEM_LENGTH, diagnose_problem
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
    await state.set_state(Diagnostics.waiting_for_problem_description)
    await message.answer(PROMPT_MESSAGE, reply_markup=ReplyKeyboardRemove())


@router.message(Diagnostics.waiting_for_problem_description)
async def receive_problem(message: Message, state: FSMContext) -> None:
    problem = (message.text or "").strip()
    if not problem or len(problem) > MAX_PROBLEM_LENGTH or problem.startswith("/") or problem in MENU_LABELS:
        await message.answer(f"Опишите проблему текстом от 1 до {MAX_PROBLEM_LENGTH} символов или отправьте /cancel.")
        return
    # Consume the draft before I/O: no queued message or retry reuses the request.
    # On errors users can reopen diagnostics; the description is never stored.
    await state.clear()
    try:
        answer = await asyncio.to_thread(diagnose_problem, problem)
    except DiagnosticInputError as error:
        await state.set_state(Diagnostics.waiting_for_problem_description)
        await message.answer(str(error))
        return
    except DiagnosticError:
        logger.error("Diagnostic request failed; AI provider unavailable.")
        await message.answer(ERROR_MESSAGE, reply_markup=main_menu_keyboard())
        return
    # Bound UTF-16 length conservatively; send plain text, never model-generated markup.
    for offset in range(0, len(answer), 2000):
        await message.answer(answer[offset:offset + 2000], parse_mode=None)
    await message.answer(FOLLOWUP_MESSAGE, reply_markup=main_menu_keyboard())
