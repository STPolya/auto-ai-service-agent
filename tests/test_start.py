"""Telegram boundary tests; no Telegram API or real database access."""

import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat, Message, User

from app.bot.handlers.start import (
    MISSING_USER_MESSAGE, SYNC_ERROR_MESSAGE, WELCOME_MESSAGE, WELCOME_IMAGE_PATH, handle_start,
)
from app.bot.keyboards.main_menu import main_menu_keyboard
from app.services.user_service import UserSyncError


class StartTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Tests remain independent of whether the developer has supplied an image.
        patcher = patch("app.bot.handlers.start.WELCOME_IMAGE_PATH")
        self.image_path = patcher.start()
        self.image_path.is_file.return_value = False
        self.addCleanup(patcher.stop)

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

    def test_russian_welcome_and_asset_path(self):
        self.assertEqual(WELCOME_MESSAGE, "Добро пожаловать в AutoCare! 🚗\n\n"
                         "Я — AI-помощник автосервиса. Помогу разобраться в возможных причинах неисправности "
                         "автомобиля, расскажу об услугах и ценах, а также помогу оформить запись на обслуживание.\n\n"
                         "Выберите нужный раздел в меню ниже 👇")
        self.assertEqual(WELCOME_IMAGE_PATH, Path(__file__).resolve().parents[1] / "assets" / "welcome.png")

    async def test_image_caption_is_sent_once_with_menu(self):
        with patch("app.bot.handlers.start.WELCOME_IMAGE_PATH", WELCOME_IMAGE_PATH):
            with patch.object(Path, "is_file", return_value=True):
                with patch("app.bot.handlers.start.sync_user"):
                    with patch.object(Message, "answer_photo", new_callable=AsyncMock) as photo:
                        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                            await handle_start(self.message())
        photo.assert_awaited_once()
        self.assertEqual(photo.call_args.args[0].path, WELCOME_IMAGE_PATH)
        self.assertEqual(photo.call_args.kwargs["caption"], WELCOME_MESSAGE)
        self.assertEqual(photo.call_args.kwargs["reply_markup"], main_menu_keyboard())
        answer.assert_not_awaited()

    async def test_missing_image_falls_back_to_text(self):
        with patch("app.bot.handlers.start.sync_user"):
            with patch.object(Message, "answer_photo", new_callable=AsyncMock) as photo:
                with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                    await handle_start(self.message())
        photo.assert_not_awaited()
        answer.assert_awaited_once_with(WELCOME_MESSAGE, reply_markup=main_menu_keyboard())


if __name__ == "__main__":
    unittest.main()
