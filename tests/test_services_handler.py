"""Catalog presentation and actual router dispatch, without external I/O."""

import threading
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from aiogram import Dispatcher
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Chat, Message, Update

from app.bot.handlers.services import EMPTY_MESSAGE, ERROR_MESSAGE, format_price, handle_services
from app.bot.keyboards.main_menu import SERVICES_BUTTON
from app.database.models import Service
from app.services.service_catalog import CatalogError


class ServicesHandlerTests(unittest.IsolatedAsyncioTestCase):
    def message(self, text=SERVICES_BUTTON):
        return Message(message_id=1, date=datetime.now(timezone.utc),
                       chat=Chat(id=123, type="private"), text=text)

    def test_decimal_price_formatting(self):
        self.assertEqual(format_price(Decimal("79")), "от €79.00")
        self.assertEqual(format_price(Decimal("79.5")), "от €79.50")
        self.assertEqual(format_price(None), "Цена по запросу")

    async def test_empty_response(self):
        with patch("app.bot.handlers.services.get_active_services", return_value=[]):
            with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                await handle_services(self.message())
                answer.assert_awaited_once_with(EMPTY_MESSAGE)

    async def test_failure_is_safe(self):
        with patch("app.bot.handlers.services.get_active_services", side_effect=CatalogError("private-detail")):
            with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                with self.assertLogs("app.bot.handlers.services", level="ERROR") as logs:
                    await handle_services(self.message())
                answer.assert_awaited_once_with(ERROR_MESSAGE)
                self.assertNotIn("private-detail", " ".join(logs.output))

    async def test_registered_button_routes_catalog_and_uses_worker(self):
        import main

        loop_thread = threading.get_ident()
        worker_threads = []

        def load():
            worker_threads.append(threading.get_ident())
            return [Service(name="Test service", price_from=Decimal("12.50"),
                            duration_minutes=35, description="Database description")]

        async def inspect_polling(dispatcher, bot, **kwargs):
            self.assertIn(main.vehicles_router, dispatcher.sub_routers)
            self.assertIn(main.booking_router, dispatcher.sub_routers)
            self.assertIn(main.appointments_router, dispatcher.sub_routers)
            self.assertIn(main.diagnostics_router, dispatcher.sub_routers)
            self.assertIn(main.handoff_router, dispatcher.sub_routers)
            self.assertIsInstance(dispatcher.fsm.events_isolation, main.SimpleEventIsolation)
            with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                await dispatcher.feed_update(bot, Update(update_id=1, message=self.message()))
                answer.assert_awaited_once_with(
                    "🔧 Test service\nот €12.50\nПримерно 35 мин\nDatabase description", parse_mode=None,
                )
                answer.reset_mock()
                result = await dispatcher.feed_update(bot, Update(update_id=2, message=self.message("Services & prices")))
                self.assertIs(result, UNHANDLED)
                answer.assert_not_awaited()

        with patch.object(main, "get_bot_token", return_value="123456:offline-placeholder"):
            with patch.object(Dispatcher, "start_polling", inspect_polling):
                with patch("app.bot.handlers.services.get_active_services", side_effect=load):
                    await main.main()
        self.assertNotEqual(worker_threads[0], loop_thread)

    async def test_long_descriptions_are_split_without_loss(self):
        service = Service(name="Long", price_from=None, duration_minutes=30, description="x" * 5000)
        with patch("app.bot.handlers.services.get_active_services", return_value=[service]):
            with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                await handle_services(self.message())
                chunks = [call.args[0] for call in answer.await_args_list]
                self.assertTrue(all(len(chunk) <= 2000 for chunk in chunks))
                self.assertEqual("".join(chunks), "🔧 Long\nЦена по запросу\nПримерно 30 мин\n" + "x" * 5000)


if __name__ == "__main__":
    unittest.main()
