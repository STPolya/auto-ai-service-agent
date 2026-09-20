"""Diagnostic FSM and Telegram boundary, with AI calls mocked."""

import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, User

from app.ai.service import DiagnosticError
from app.bot.handlers import diagnostics
from app.bot.keyboards.main_menu import DIAGNOSTICS_BUTTON, main_menu_keyboard
from app.bot.states.diagnostics import Diagnostics


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = MemoryStorage()
        self.state = FSMContext(self.storage, StorageKey(bot_id=123456, chat_id=100, user_id=100))
        self.bot = Bot(token="123456:offline-placeholder")
        self.addAsyncCleanup(self.storage.close)
        self.addAsyncCleanup(self.bot.session.close)
        patcher = patch.object(Message, "answer", new_callable=AsyncMock)
        self.answer = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("app.bot.handlers.diagnostics.diagnose_problem", return_value="Возможные причины:\n- Нужен осмотр.")
        self.diagnose = patcher.start()
        self.addCleanup(patcher.stop)

    async def send(self, text):
        message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=100, type="private"),
                          from_user=User(id=100, is_bot=False, first_name="Alex"), text=text)
        await diagnostics.router.propagate_event("message", message, state=self.state,
                                                 raw_state=await self.state.get_state(), bot=self.bot)

    async def test_menu_prompts_and_sets_single_state(self):
        await self.send(DIAGNOSTICS_BUTTON)
        self.assertEqual(await self.state.get_state(), Diagnostics.waiting_for_problem_description.state)
        self.assertEqual(self.answer.await_args.args[0], diagnostics.PROMPT_MESSAGE)
        self.diagnose.assert_not_called()

    async def test_user_problem_is_sent_once_in_worker_then_booking_is_offered(self):
        workers = []
        def diagnose(problem):
            workers.append(threading.get_ident())
            return "Возможные причины:\n- Нужен осмотр."
        self.diagnose.side_effect = diagnose
        await self.send(DIAGNOSTICS_BUTTON)
        await self.send("  Шум при торможении  ")
        await self.send("Ещё одно сообщение")
        self.diagnose.assert_called_once_with("Шум при торможении")
        self.assertNotEqual(workers[0], threading.get_ident())
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})
        self.answer.assert_awaited_with(diagnostics.FOLLOWUP_MESSAGE, reply_markup=main_menu_keyboard())

    async def test_empty_nontext_and_oversized_messages_keep_input_state(self):
        await self.send(DIAGNOSTICS_BUTTON)
        for text in (None, "", "   ", "x" * 3001):
            await self.send(text)
            self.assertEqual(await self.state.get_state(), Diagnostics.waiting_for_problem_description.state)
        self.diagnose.assert_not_called()

    async def test_cancel_clears_state_without_ai_call(self):
        await self.send(DIAGNOSTICS_BUTTON)
        await self.send("/cancel")
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})
        self.diagnose.assert_not_called()
        self.assertEqual(self.answer.await_args.args[0], "Описание проблемы отменено.")

    async def test_error_and_logs_do_not_expose_fake_key(self):
        secret = "unit-test-key-not-real"
        self.diagnose.side_effect = DiagnosticError(secret)
        await self.send(DIAGNOSTICS_BUTTON)
        with self.assertLogs("app.bot.handlers.diagnostics", level="ERROR") as logs:
            await self.send("Не заводится")
        self.assertNotIn(secret, " ".join(logs.output))
        self.assertEqual(self.answer.await_args.args[0], diagnostics.ERROR_MESSAGE)
        self.assertIsNone(await self.state.get_state())
        self.assertFalse(any(call.args[0] == diagnostics.FOLLOWUP_MESSAGE for call in self.answer.await_args_list))

    async def test_other_commands_are_not_sent_to_ai(self):
        await self.send(DIAGNOSTICS_BUTTON)
        await self.send("/start")
        self.diagnose.assert_not_called()

    async def test_long_unicode_reply_is_split_as_plain_text(self):
        self.diagnose.return_value = "🚗" * 2800
        await self.send(DIAGNOSTICS_BUTTON)
        self.answer.reset_mock()
        await self.send("Проблема")
        calls = self.answer.await_args_list[:-1]
        self.assertEqual("".join(call.args[0] for call in calls), "🚗" * 2800)
        self.assertTrue(all(len(call.args[0].encode("utf-16-le")) // 2 <= 4096 for call in calls))
        self.assertTrue(all(call.kwargs["parse_mode"] is None for call in calls))
