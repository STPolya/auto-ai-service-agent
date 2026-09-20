"""Telegram callback/FSM tests; no Telegram, Gemini or database connections."""

import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User

from app.bot.handlers import diagnostics, handoff
from app.bot.keyboards.handoff import HANDOFF_BUTTON, handoff_keyboard
from app.bot.states.diagnostics import Diagnostics
from app.services.support_request_service import SupportRequestError


class HandoffHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = MemoryStorage()
        self.state = FSMContext(self.storage, StorageKey(bot_id=123456, chat_id=100, user_id=100))
        self.bot = Bot(token="123456:offline-placeholder")
        self.addAsyncCleanup(self.storage.close)
        self.addAsyncCleanup(self.bot.session.close)
        for target, name in (("aiogram.types.Message.answer", "answer"), ("aiogram.types.CallbackQuery.answer", "ack")):
            patcher = patch(target, new_callable=AsyncMock)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        patcher = patch("app.bot.handlers.handoff.create_support_request", return_value=SimpleNamespace(created=True))
        self.create = patcher.start()
        self.addCleanup(patcher.stop)
        await self.state.set_state(Diagnostics.waiting_for_problem_description)
        await self.state.update_data(diagnostic_conversation_id=42)
        self.message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=100, type="private"),
                               from_user=User(id=100, first_name="Alex", is_bot=False), text="На скорости")

    async def press(self, data="handoff:42"):
        callback = CallbackQuery(id="offline", from_user=self.message.from_user, chat_instance="offline",
                                 message=self.message, data=data)
        await handoff.router.propagate_event("callback_query", callback, state=self.state,
                                            raw_state=await self.state.get_state(), bot=self.bot)

    async def test_success_runs_in_thread_retains_fsm_and_diagnostics_work_afterwards(self):
        workers = []
        def create(telegram_id, conversation_id):
            workers.append(threading.get_ident())
            return SimpleNamespace(created=True)
        self.create.side_effect = create
        await self.press()
        self.create.assert_called_once_with(100, 42)
        self.assertNotEqual(workers[0], threading.get_ident())
        self.answer.assert_awaited_with(handoff.SUCCESS_MESSAGE)
        self.assertEqual(await self.state.get_data(), {"diagnostic_conversation_id": 42})
        self.assertEqual(await self.state.get_state(), Diagnostics.waiting_for_problem_description.state)
        with patch("app.bot.handlers.diagnostics.diagnostic_turn", return_value="Ответ") as turn:
            await diagnostics.receive_problem(self.message, self.state)
        turn.assert_called_once_with(100, 42, "На скорости")

    async def test_duplicate_is_friendly(self):
        self.create.return_value = SimpleNamespace(created=False)
        await self.press()
        self.answer.assert_awaited_with(handoff.DUPLICATE_MESSAGE)

    async def test_stale_forged_and_cancelled_callbacks_never_create_request(self):
        for data in ("handoff:1", "handoff:42junk", "handoff:"):
            await self.press(data)
            self.answer.assert_awaited_with(handoff.STALE_MESSAGE)
        await self.state.clear()
        await self.press()
        self.answer.assert_awaited_with(handoff.STALE_MESSAGE)
        self.create.assert_not_called()

    async def test_failure_is_safe_and_retains_conversation(self):
        self.create.side_effect = SupportRequestError("private token")
        with self.assertLogs("app.bot.handlers.handoff", level="ERROR") as logs:
            await self.press()
        self.assertNotIn("private token", " ".join(logs.output))
        self.answer.assert_awaited_with(handoff.ERROR_MESSAGE)
        self.assertEqual(await self.state.get_data(), {"diagnostic_conversation_id": 42})

    async def test_inline_button_uses_russian_label_and_current_conversation(self):
        button = handoff_keyboard(42).inline_keyboard[0][0]
        self.assertEqual(button.text, HANDOFF_BUTTON)
        self.assertEqual(button.text, "👨‍💼 Связаться с оператором")
        self.assertEqual(button.callback_data, "handoff:42")
        with patch("app.bot.handlers.diagnostics.create_conversation", return_value=43):
            await diagnostics.begin_diagnostics(self.message, self.state)
        self.assertEqual(self.answer.await_args.kwargs["reply_markup"], handoff_keyboard(43))
