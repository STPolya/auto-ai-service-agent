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
        patcher = patch("app.bot.handlers.diagnostics.diagnostic_turn", return_value="Возможные причины:\n- Нужен осмотр.")
        self.diagnose = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("app.bot.handlers.diagnostics.create_conversation", return_value=42)
        self.create = patcher.start()
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
        self.create.assert_called_once_with(100, None, "Alex")
        self.assertEqual(await self.state.get_data(), {"diagnostic_conversation_id": 42})

    async def test_user_problem_is_sent_once_in_worker_then_booking_is_offered(self):
        workers = []
        def diagnose(telegram_id, conversation_id, problem):
            workers.append(threading.get_ident())
            return "Возможные причины:\n- Нужен осмотр."
        self.diagnose.side_effect = diagnose
        await self.send(DIAGNOSTICS_BUTTON)
        await self.send("  Шум при торможении  ")
        await self.send("Ещё одно сообщение")
        self.assertEqual(self.diagnose.call_count, 2)
        self.assertNotEqual(workers[0], threading.get_ident())
        self.assertEqual(await self.state.get_state(), Diagnostics.waiting_for_problem_description.state)
        self.assertEqual(await self.state.get_data(), {"diagnostic_conversation_id": 42})
        self.diagnose.assert_any_call(100, 42, "Шум при торможении")
        self.diagnose.assert_any_call(100, 42, "Ещё одно сообщение")
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

    async def test_cancel_retains_persisted_history_and_restart_uses_new_row(self):
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from app.database.base import Base
        from app.database.models import Message as SavedMessage
        from app.services import conversation_service

        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        self.create.side_effect = conversation_service.create_conversation
        self.diagnose.side_effect = conversation_service.diagnostic_turn
        with patch("app.services.conversation_service.get_session_factory", return_value=factory), \
             patch("app.services.user_service.get_session_factory", return_value=factory), \
             patch("app.services.conversation_service.diagnose_problem", return_value="Ответ"):
            await self.send(DIAGNOSTICS_BUTTON)
            first_id = (await self.state.get_data())["diagnostic_conversation_id"]
            await self.send("Вибрация")
            await self.send("/cancel")
            self.assertEqual(await self.state.get_data(), {})
            with factory() as session:
                rows = list(session.scalars(select(SavedMessage).order_by(SavedMessage.id)))
                self.assertEqual([row.content for row in rows], ["Вибрация", "Ответ"])
            await self.send(DIAGNOSTICS_BUTTON)
            second_id = (await self.state.get_data())["diagnostic_conversation_id"]
            self.assertNotEqual(first_id, second_id)
            self.assertEqual(conversation_service.recent_messages(100, second_id), [])

    async def test_error_and_logs_do_not_expose_fake_key(self):
        secret = "unit-test-key-not-real"
        self.diagnose.side_effect = DiagnosticError(secret)
        await self.send(DIAGNOSTICS_BUTTON)
        with self.assertLogs("app.bot.handlers.diagnostics", level="ERROR") as logs:
            await self.send("Не заводится")
        self.assertNotIn(secret, " ".join(logs.output))
        self.assertEqual(self.answer.await_args.args[0], diagnostics.ERROR_MESSAGE)
        self.assertEqual(await self.state.get_state(), Diagnostics.waiting_for_problem_description.state)
        self.assertFalse(any(call.args[0] == diagnostics.FOLLOWUP_MESSAGE for call in self.answer.await_args_list))

    async def test_other_commands_are_not_sent_to_ai(self):
        await self.send(DIAGNOSTICS_BUTTON)
        await self.send("/start")
        self.diagnose.assert_not_called()
        self.assertEqual(await self.state.get_data(), {})
        self.assertIsNone(await self.state.get_state())

    async def test_restart_creates_new_conversation(self):
        self.create.side_effect = [42, 43]
        await self.send(DIAGNOSTICS_BUTTON)
        await self.send("Проблема")
        await self.send(DIAGNOSTICS_BUTTON)
        self.assertEqual(await self.state.get_data(), {"diagnostic_conversation_id": 43})

    async def test_menu_navigation_clears_conversation(self):
        for label in diagnostics.MENU_LABELS:
            if label == DIAGNOSTICS_BUTTON:
                continue
            await self.send(DIAGNOSTICS_BUTTON)
            await self.send(label)
            self.assertIsNone(await self.state.get_state())
            self.assertEqual(await self.state.get_data(), {})
        self.diagnose.assert_not_called()

    async def test_creation_failure_is_safe_and_leaves_no_active_conversation(self):
        self.create.side_effect = diagnostics.ConversationError("private database detail")
        with self.assertLogs("app.bot.handlers.diagnostics", level="ERROR") as logs:
            await self.send(DIAGNOSTICS_BUTTON)
        self.assertNotIn("private database detail", " ".join(logs.output))
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})
        self.assertEqual(self.answer.await_args.args[0], diagnostics.ERROR_MESSAGE)

    async def test_long_unicode_reply_is_split_as_plain_text(self):
        self.diagnose.return_value = "🚗" * 2800
        await self.send(DIAGNOSTICS_BUTTON)
        self.answer.reset_mock()
        await self.send("Проблема")
        calls = self.answer.await_args_list[:-1]
        self.assertEqual("".join(call.args[0] for call in calls), "🚗" * 2800)
        self.assertTrue(all(len(call.args[0].encode("utf-16-le")) // 2 <= 4096 for call in calls))
        self.assertTrue(all(call.kwargs["parse_mode"] is None for call in calls))
