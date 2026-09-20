"""Telegram-only handoff boundary; no database or provider details."""

import asyncio
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.handoff import HANDOFF_PREFIX
from app.bot.states.diagnostics import Diagnostics
from app.services.support_request_service import SupportRequestError, create_support_request

router = Router(name="handoff")
router.callback_query.filter(F.message.chat.type == "private")
logger = logging.getLogger(__name__)
SUCCESS_MESSAGE = "Запрос оператору создан ✅\nИстория текущего диалога сохранена, чтобы специалист мог ознакомиться с контекстом."
DUPLICATE_MESSAGE = "Запрос оператору уже создан. Специалист сможет продолжить работу с вашим обращением."
ERROR_MESSAGE = "Не удалось создать запрос оператору. Попробуйте позже."
STALE_MESSAGE = "Эта кнопка относится к завершённому диалогу. Откройте «🤖 Описать проблему» и используйте кнопку в текущем диалоге."


@router.callback_query(F.data.startswith(HANDOFF_PREFIX))
async def request_handoff(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    conversation_id = (await state.get_data()).get("diagnostic_conversation_id")
    if (await state.get_state() != Diagnostics.waiting_for_problem_description.state
            or type(conversation_id) is not int
            or callback.data != f"{HANDOFF_PREFIX}{conversation_id}"):
        await callback.message.answer(STALE_MESSAGE)
        return
    try:
        result = await asyncio.to_thread(create_support_request, callback.from_user.id, conversation_id)
    except SupportRequestError:
        logger.error("Support request failed: category=persistence_or_ownership.")
        await callback.message.answer(ERROR_MESSAGE)
        return
    # Handoff is a snapshot/reference; leave FSM and original history intact.
    await callback.message.answer(SUCCESS_MESSAGE if result.created else DUPLICATE_MESSAGE)
