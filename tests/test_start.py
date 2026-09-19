"""Telegram boundary tests; no Telegram API or real database access."""

import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat, Message, User

from app.bot.handlers.start import (
    MISSING_USER_MESSAGE, SYNC_ERROR_MESSAGE, WELCOME_MESSAGE, handle_start,
)
from app.bot.keyboards.main_menu import main_menu_keyboard
from app.services.user_service import UserSyncError


class StartTests(unittest.IsolatedAsyncioTestCase):
    def message(self, has_user=True):
        return Message(
            message_id=1, date=datetime.now(timezone.utc),
            chat=Chat(id=123, type="private"), text="/start",
            from_user=User(id=123, is_bot=False, first_name="Alex", username="driver") if has_user else None,
        )

    async def test_success_keeps_welcome_and_menu_and_uses_worker_thread(self):
        loop_thread = threading.get_ident()
        worker_threads = []

        def synchronize(**kwargs):
            worker_threads.append(threading.get_ident())

        with patch("app.bot.handlers.start.sync_user", side_effect=synchronize) as sync:
            with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                await handle_start(self.message())
                sync.assert_called_once_with(telegram_id=123, username="driver", first_name="Alex")
                answer.assert_awaited_once_with(WELCOME_MESSAGE, reply_markup=main_menu_keyboard())
        self.assertNotEqual(worker_threads[0], loop_thread)

    async def test_failure_sends_friendly_error_and_safe_log(self):
        with patch("app.bot.handlers.start.sync_user", side_effect=UserSyncError("private-driver-detail")):
            with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                with self.assertLogs("app.bot.handlers.start", level="ERROR") as logs:
                    await handle_start(self.message())
                answer.assert_awaited_once_with(SYNC_ERROR_MESSAGE)
                self.assertNotIn("private-driver-detail", " ".join(logs.output))
                self.assertNotIn("private-driver-detail", str(answer.call_args))

    async def test_missing_sender_does_not_sync(self):
        with patch("app.bot.handlers.start.sync_user") as sync:
            with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                await handle_start(self.message(has_user=False))
                sync.assert_not_called()
                answer.assert_awaited_once_with(MISSING_USER_MESSAGE)

    async def test_programming_errors_are_not_swallowed(self):
        with patch("app.bot.handlers.start.sync_user", side_effect=RuntimeError("unexpected")):
            with self.assertRaises(RuntimeError):
                await handle_start(self.message())


if __name__ == "__main__":
    unittest.main()
