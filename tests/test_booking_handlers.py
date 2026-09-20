"""Booking routes and FSM with real memory state and mocked database I/O."""

import asyncio
import threading
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot.handlers import appointments, booking
from app.bot.keyboards.main_menu import APPOINTMENTS_BUTTON, BOOKING_BUTTON
from app.bot.keyboards.vehicles import add_vehicle_keyboard
from app.bot.states.booking import Booking
from app.database.models import Appointment, Service, Vehicle
from app.services.appointment_service import AppointmentError
from app.services.booking_rules import BOOKING_TIMEZONE, BookingValidationError
from app.services.vehicle_service import VehicleError

NOW = datetime(2026, 9, 19, 8, tzinfo=BOOKING_TIMEZONE)


class BookingHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = MemoryStorage()
        self.state = FSMContext(self.storage, StorageKey(bot_id=123456, chat_id=100, user_id=100))
        self.bot = Bot(token="123456:offline-placeholder")
        self.addAsyncCleanup(self.bot.session.close)
        self.addAsyncCleanup(self.storage.close)
        self.vehicle = Vehicle(id=1, user_id=1, brand="Toyota", model="Corolla", year=2020)
        self.service = Service(id=1, name="Oil change", price_from=Decimal("79.00"), duration_minutes=60)
        self.answer = self.mock(patch.object(Message, "answer", new_callable=AsyncMock))
        self.callback_answer = self.mock(patch.object(CallbackQuery, "answer", new_callable=AsyncMock))
        self.mock(patch("app.services.booking_rules.local_now", return_value=NOW))
        self.vehicles = self.mock(patch("app.bot.handlers.booking.list_vehicles", return_value=[self.vehicle]))
        self.services = self.mock(patch("app.bot.handlers.booking.get_active_services", return_value=[self.service]))
        self.selection = self.mock(patch("app.bot.handlers.booking.get_booking_selection", return_value=(self.vehicle, self.service)))
        self.create = self.mock(patch("app.bot.handlers.booking.create_appointment", return_value=Appointment(id=1)))
        self.upcoming = self.mock(patch("app.bot.handlers.appointments.list_upcoming_appointments", return_value=[]))

    def mock(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def message(self, text):
        return Message(message_id=1, date=NOW, chat=Chat(id=100, type="private"), text=text,
                       from_user=User(id=100, is_bot=False, first_name="Alex", username="driver"))

    async def send(self, text):
        await booking.router.propagate_event("message", self.message(text), state=self.state,
                                             raw_state=await self.state.get_state(), bot=self.bot)

    async def callback(self, action, identifier=1, token=None):
        data = await self.state.get_data()
        query = CallbackQuery(id="test", from_user=self.message("").from_user, chat_instance="offline",
                              message=self.message("buttons"),
                              data=f"book:{token or data.get('token', 'expired')}:{action}:{identifier}")
        await booking.router.propagate_event("callback_query", query, state=self.state, bot=self.bot)

    async def to_time(self):
        await self.send(BOOKING_BUTTON)
        await self.callback("vehicle")
        await self.callback("service")
        await self.send("25.09.2026")

    async def to_confirmation(self):
        await self.to_time()
        await self.callback("time", 870)

    async def test_no_vehicles_opens_existing_add_flow_without_booking_state(self):
        self.vehicles.return_value = []
        await self.send(BOOKING_BUTTON)
        self.assertEqual(self.answer.await_args.kwargs["reply_markup"], add_vehicle_keyboard())
        self.assertIsNone(await self.state.get_state())
        self.create.assert_not_called()

    async def test_flow_review_does_not_persist_and_prices_come_from_service(self):
        await self.send(BOOKING_BUTTON)
        self.vehicles.assert_called_once_with(100, "driver", "Alex")
        self.assertEqual(await self.state.get_state(), Booking.vehicle.state)
        self.assertIn("Toyota Corolla", self.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text)
        await self.callback("vehicle")
        self.assertEqual(await self.state.get_state(), Booking.service.state)
        self.assertIn("€79.00", self.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text)
        await self.callback("service")
        self.assertEqual(await self.state.get_state(), Booking.date.state)
        await self.send("25.09.2026")
        self.assertEqual(await self.state.get_state(), Booking.time.state)
        await self.callback("time", 870)
        self.assertEqual(await self.state.get_state(), Booking.confirmation.state)
        self.create.assert_not_called()
        summary = self.answer.await_args.args[0]
        for text in ("Toyota Corolla (2020)", "Замена масла", "€79.00", "25.09.2026", "14:30", "МСК"):
            self.assertIn(text, summary)
        self.assertNotIn("MVP", summary)
        self.assertNotIn("capacity", summary)
        self.assertNotIn("слот", summary)
        buttons = self.answer.await_args.kwargs["reply_markup"].inline_keyboard[0]
        self.assertEqual([b.text for b in buttons], ["✅ Подтвердить", "❌ Отменить"])

    async def test_invalid_date_and_time_keep_steps(self):
        await self.send(BOOKING_BUTTON)
        await self.callback("vehicle")
        await self.callback("service")
        await self.send("31.09.2026")
        self.assertEqual(await self.state.get_state(), Booking.date.state)
        await self.send("25.09.2026")
        await self.callback("time", 855)
        self.assertEqual(await self.state.get_state(), Booking.time.state)
        self.create.assert_not_called()

    async def test_forged_vehicle_is_rejected(self):
        await self.send(BOOKING_BUTTON)
        await self.callback("vehicle", 999)
        self.selection.assert_not_called()
        self.create.assert_not_called()
        self.assertIsNone(await self.state.get_state())

    async def test_inactive_or_missing_service_callback_is_safe(self):
        for identifier in (2, 999):
            await self.send(BOOKING_BUTTON)
            await self.callback("vehicle")
            self.selection.side_effect = BookingValidationError("Эта услуга больше недоступна. Начните запись заново.")
            await self.callback("service", identifier)
            self.assertIsNone(await self.state.get_state())
            self.assertIn("недоступна", self.answer.await_args.args[0])
        self.create.assert_not_called()

    async def test_no_active_services_clears_draft(self):
        self.services.return_value = []
        await self.send(BOOKING_BUTTON)
        await self.callback("vehicle")
        self.assertIsNone(await self.state.get_state())
        self.assertIn("нет доступных услуг", self.answer.await_args.args[0])

    async def test_confirmation_creates_once_with_aware_time_on_worker_thread(self):
        await self.to_confirmation()
        token = (await self.state.get_data())["token"]
        workers = []
        self.create.side_effect = lambda *args: workers.append(threading.get_ident())
        await self.callback("confirm", token=token)
        await self.callback("confirm", token=token)
        self.create.assert_called_once_with(100, 1, 1, datetime(2026, 9, 25, 14, 30, tzinfo=BOOKING_TIMEZONE))
        self.assertNotEqual(workers[0], threading.get_ident())
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})
        self.assertIn("Наш менеджер свяжется с вами для подтверждения записи", self.answer.await_args.args[0])

    async def test_old_confirm_cannot_confirm_new_draft(self):
        await self.to_confirmation()
        old_token = (await self.state.get_data())["token"]
        await self.to_confirmation()
        await self.callback("confirm", token=old_token)
        self.create.assert_not_called()
        self.assertEqual(await self.state.get_state(), Booking.confirmation.state)

    async def test_both_cancel_paths_clear_without_insert(self):
        await self.to_confirmation()
        await self.callback("cancel")
        self.assertEqual(await self.state.get_data(), {})
        self.assertIsNone(await self.state.get_state())
        await self.to_time()
        await self.send("/cancel")
        self.assertEqual(await self.state.get_data(), {})
        self.assertIsNone(await self.state.get_state())
        self.create.assert_not_called()
        self.assertEqual(self.answer.await_args.args[0], "Запись отменена.")

    async def test_save_failure_is_safe_and_never_success(self):
        await self.to_confirmation()
        self.create.side_effect = AppointmentError("private-detail")
        with self.assertLogs("app.bot.handlers.booking", level="ERROR") as logs:
            await self.callback("confirm")
        self.assertEqual(self.answer.await_args.args[0], booking.SAVE_ERROR_MESSAGE)
        self.assertNotIn("private-detail", " ".join(logs.output))
        self.assertIsNone(await self.state.get_state())

    async def test_loading_failure_is_safe(self):
        self.vehicles.side_effect = VehicleError("private-detail")
        with self.assertLogs("app.bot.handlers.booking", level="ERROR") as logs:
            await self.send(BOOKING_BUTTON)
        self.assertEqual(self.answer.await_args.args[0], booking.ERROR_MESSAGE)
        self.assertNotIn("private-detail", " ".join(logs.output))

    async def test_upcoming_empty_and_safe_failure(self):
        await appointments.show_appointments(self.message(APPOINTMENTS_BUTTON), self.state)
        self.assertEqual(self.answer.await_args.args[0], appointments.EMPTY_MESSAGE)
        self.upcoming.side_effect = AppointmentError("private-detail")
        with self.assertLogs("app.bot.handlers.appointments", level="ERROR") as logs:
            await appointments.show_appointments(self.message(APPOINTMENTS_BUTTON), self.state)
        self.assertEqual(self.answer.await_args.args[0], appointments.ERROR_MESSAGE)
        self.assertNotIn("private-detail", " ".join(logs.output))

    async def test_upcoming_display_uses_moscow(self):
        self.upcoming.return_value = [Appointment(
            vehicle=self.vehicle, service=self.service, status="scheduled",
            appointment_at=datetime(2026, 9, 25, 12, 30, tzinfo=timezone.utc),
        )]
        await appointments.show_appointments(self.message(APPOINTMENTS_BUTTON), self.state)
        self.upcoming.assert_called_once_with(100)
        for text in ("Toyota Corolla", "Замена масла", "25.09.2026", "15:30", "Ожидает подтверждения менеджером", "МСК"):
            self.assertIn(text, self.answer.await_args.args[0])
        self.assertNotIn("scheduled", self.answer.await_args.args[0])

    async def test_time_keyboard_and_manual_time_does_not_advance(self):
        await self.to_time()
        self.assertEqual(self.answer.await_args.args[0], "Выберите удобное время (МСК):")
        buttons = [b for row in self.answer.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertEqual([b.text for b in buttons[:-1]], [f"{h:02}:{m:02}" for h in range(9, 18) for m in (0, 30)])
        self.assertTrue(all(":time:" in b.callback_data for b in buttons[:-1]))
        await self.send("14:30")
        self.assertEqual(await self.state.get_state(), Booking.time.state)
        self.create.assert_not_called()

    async def test_no_times_today_returns_to_date_step_and_accepts_tomorrow(self):
        await self.to_time()
        await self.callback("date", 0)
        with patch("app.services.booking_rules.local_now", return_value=NOW.replace(hour=18)):
            await self.send("19.09.2026")
            self.assertEqual(await self.state.get_state(), Booking.date.state)
            self.assertIn(booking.NO_TIMES_MESSAGE, self.answer.await_args.args[0])
            await self.send("20.09.2026")
        self.assertEqual(await self.state.get_state(), Booking.time.state)

    async def test_past_or_forged_time_callback_is_rejected(self):
        await self.to_time()
        for minutes in (510, 855, 1080, 99999999):
            await self.callback("time", minutes)
            self.assertEqual(await self.state.get_state(), Booking.time.state)
        await self.callback("date", 0)
        await self.send("19.09.2026")
        with patch("app.services.booking_rules.local_now", return_value=NOW.replace(hour=10)):
            await self.callback("time", 540)
            self.assertEqual(await self.state.get_state(), Booking.time.state)
        self.create.assert_not_called()

    async def test_time_callback_after_last_start_recovers_to_date_step(self):
        await self.to_time()
        await self.callback("date", 0)
        await self.send("19.09.2026")
        with patch("app.services.booking_rules.local_now", return_value=NOW.replace(hour=18)):
            await self.callback("time", 1050)
        self.assertEqual(await self.state.get_state(), Booking.date.state)
        self.assertIn(booking.NO_TIMES_MESSAGE, self.answer.await_args.args[0])

    async def test_time_button_from_previous_date_is_stale(self):
        await self.to_time()
        token = (await self.state.get_data())["token"]
        await self.callback("date", 0)
        await self.send("26.09.2026")
        await self.callback("time", 870, token=token)
        self.assertEqual(await self.state.get_state(), Booking.time.state)
        self.callback_answer.assert_awaited_with(booking.STALE_MESSAGE)

    async def test_concurrent_confirmations_create_once(self):
        await self.to_confirmation()
        token = (await self.state.get_data())["token"]
        dispatcher = Dispatcher(storage=self.storage, events_isolation=SimpleEventIsolation())
        dispatcher.callback_query.register(booking.booking_callback, F.data.startswith("book:"))
        self.addAsyncCleanup(dispatcher.fsm.events_isolation.close)
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def create(*args):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(timeout=5):
                raise AssertionError("Worker not released")

        self.create.side_effect = create
        def update(identifier):
            return Update(update_id=identifier, callback_query=CallbackQuery(
                id=str(identifier), from_user=self.message("").from_user, chat_instance="offline",
                message=self.message("buttons"), data=f"book:{token}:confirm:0",
            ))
        first = asyncio.create_task(dispatcher.feed_update(self.bot, update(1)))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            second = asyncio.create_task(dispatcher.feed_update(self.bot, update(2)))
        finally:
            release.set()
        await first
        await second
        self.create.assert_called_once()
