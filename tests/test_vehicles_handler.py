"""FSM routing tests with real memory state; database calls are mocked."""

import asyncio
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.bot.handlers import vehicles
from app.bot.keyboards.main_menu import VEHICLES_BUTTON
from app.bot.keyboards.vehicles import ADD_VEHICLE_CALLBACK, add_vehicle_keyboard
from app.bot.states.vehicle import AddVehicle
from app.database.models import Vehicle
from app.services.vehicle_service import VehicleError


class VehiclesHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = MemoryStorage()
        self.state = FSMContext(self.storage, StorageKey(bot_id=123456, chat_id=100, user_id=100))
        self.bot = Bot(token="123456:offline-placeholder")
        self.addAsyncCleanup(self.bot.session.close)
        self.addAsyncCleanup(self.storage.close)
        patcher = patch.object(Message, "answer", new_callable=AsyncMock)
        self.answer = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(CallbackQuery, "answer", new_callable=AsyncMock)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("app.bot.handlers.vehicles.create_vehicle", return_value=Vehicle(
            id=1, brand="Toyota", model="Corolla", year=2020, license_plate=None,
        ))
        self.create = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("app.bot.handlers.vehicles.list_vehicles", return_value=[])
        self.list = patcher.start()
        self.addCleanup(patcher.stop)

    def message(self, text, has_user=True):
        return Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=100, type="private"),
                       from_user=User(id=100, is_bot=False, first_name="Alex", username="driver") if has_user else None,
                       text=text)

    async def send(self, text):
        await vehicles.router.propagate_event(
            "message", self.message(text), state=self.state,
            raw_state=await self.state.get_state(), bot=self.bot,
        )

    async def begin(self):
        callback = CallbackQuery(id="test", from_user=self.message("").from_user,
                                 chat_instance="offline", message=self.message("Add"), data=ADD_VEHICLE_CALLBACK)
        await vehicles.router.propagate_event("callback_query", callback, state=self.state, bot=self.bot)

    async def fill_to_plate(self):
        await self.begin()
        await self.send(" Toyota ")
        await self.send(" Corolla ")
        await self.send("2020")

    async def test_empty_list_has_add_button(self):
        await self.send(VEHICLES_BUTTON)
        self.answer.assert_awaited_once_with(vehicles.EMPTY_MESSAGE, reply_markup=add_vehicle_keyboard())
        self.list.assert_called_once_with(telegram_id=100, username="driver", first_name="Alex")

    async def test_list_formats_vehicle_and_offloads_database(self):
        thread = threading.get_ident()
        workers = []
        def load(**kwargs):
            workers.append(threading.get_ident())
            return [Vehicle(brand="Toyota", model="Corolla", year=2020, license_plate="AB-123-CD")]
        self.list.side_effect = load
        await self.send(VEHICLES_BUTTON)
        self.assertNotEqual(thread, workers[0])
        self.assertEqual(self.answer.await_args_list[0].args[0], "🚗 Toyota Corolla (2020)\nPlate: AB-123-CD")
        self.assertEqual(self.answer.await_args_list[-1].kwargs["reply_markup"], add_vehicle_keyboard())

    def test_nullable_vehicle_display(self):
        self.assertEqual(vehicles.format_vehicle(Vehicle(brand="Toyota", model="Corolla", year=None)),
                         "🚗 Toyota Corolla (year not specified)\nPlate: not specified")

    async def test_complete_fsm_creates_once_and_trims_plate(self):
        await self.begin()
        self.assertEqual(await self.state.get_state(), AddVehicle.brand.state)
        await self.send(" Toyota ")
        self.assertEqual(await self.state.get_state(), AddVehicle.model.state)
        await self.send(" Corolla ")
        self.assertEqual(await self.state.get_state(), AddVehicle.year.state)
        await self.send("2020")
        self.assertEqual(await self.state.get_state(), AddVehicle.license_plate.state)
        self.create.assert_not_called()
        await self.send(" AB-123-CD ")
        await self.send(" AB-123-CD ")
        self.create.assert_called_once_with(telegram_id=100, username="driver", first_name="Alex",
                                           brand="Toyota", model="Corolla", year=2020, license_plate="AB-123-CD")
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})
        self.assertTrue(self.answer.await_args.args[0].startswith("Vehicle saved!"))

    async def test_invalid_year_retains_step(self):
        await self.begin()
        await self.send("Toyota")
        await self.send("Corolla")
        for value in ("abc", "2020.5", "0", "1885", "9999", "", "２０２０"):
            await self.send(value)
            self.assertEqual(await self.state.get_state(), AddVehicle.year.state)
        self.create.assert_not_called()

    async def test_empty_and_overlong_brand_model_rejected(self):
        await self.begin()
        for value in ("   ", "x" * 101, "/start"):
            await self.send(value)
            self.assertEqual(await self.state.get_state(), AddVehicle.brand.state)
        await self.send("Toyota")
        for value in ("   ", "x" * 101):
            await self.send(value)
            self.assertEqual(await self.state.get_state(), AddVehicle.model.state)

    async def test_plate_skip_uses_none_and_worker_thread(self):
        thread = threading.get_ident()
        workers = []
        result = self.create.return_value
        def create(**kwargs):
            workers.append(threading.get_ident())
            return result
        self.create.side_effect = create
        await self.fill_to_plate()
        await self.send("/skip")
        self.assertIsNone(self.create.call_args.kwargs["license_plate"])
        self.assertNotEqual(thread, workers[0])
        self.assertIn("Plate: not specified", self.answer.await_args.args[0])

    async def test_invalid_plate_keeps_step(self):
        await self.fill_to_plate()
        for value in ("  ", "x" * 65):
            await self.send(value)
            self.assertEqual(await self.state.get_state(), AddVehicle.license_plate.state)
        self.create.assert_not_called()

    async def test_cancel_clears_data_and_does_not_save(self):
        await self.fill_to_plate()
        await self.send("/cancel")
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})
        self.assertEqual(self.answer.await_args.args[0], "Vehicle entry cancelled.")
        self.create.assert_not_called()

    async def test_save_failure_clears_state_without_success_or_secrets(self):
        await self.fill_to_plate()
        self.create.side_effect = VehicleError("private-detail")
        with self.assertLogs("app.bot.handlers.vehicles", level="ERROR") as logs:
            await self.send("/skip")
        self.assertEqual(self.answer.await_args.args[0], vehicles.SAVE_ERROR_MESSAGE)
        self.assertNotIn("private-detail", " ".join(logs.output))
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await self.state.get_data(), {})

    async def test_list_failure_is_safe(self):
        self.list.side_effect = VehicleError("private-detail")
        with self.assertLogs("app.bot.handlers.vehicles", level="ERROR") as logs:
            await self.send(VEHICLES_BUTTON)
        self.assertEqual(self.answer.await_args.args[0], vehicles.ERROR_MESSAGE)
        self.assertNotIn("private-detail", " ".join(logs.output))

    async def test_missing_sender_does_not_call_database(self):
        await vehicles.show_vehicles(self.message(VEHICLES_BUTTON, has_user=False), self.state)
        self.list.assert_not_called()

    async def test_repeated_add_does_not_reset_form(self):
        await self.begin()
        await self.send("Toyota")
        await self.begin()
        self.assertEqual(await self.state.get_state(), AddVehicle.model.state)
        self.assertEqual(await self.state.get_data(), {"brand": "Toyota"})

    async def test_concurrent_final_replies_create_once(self):
        dispatcher = Dispatcher(storage=self.storage, events_isolation=SimpleEventIsolation())
        dispatcher.message.register(vehicles.receive_plate, AddVehicle.license_plate)
        self.addAsyncCleanup(dispatcher.fsm.events_isolation.close)
        await self.fill_to_plate()
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        result = self.create.return_value

        def create(**kwargs):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(timeout=5):
                raise AssertionError("Worker was not released")
            return result

        self.create.side_effect = create
        first = asyncio.create_task(dispatcher.feed_update(self.bot, Update(update_id=1, message=self.message("ABC"))))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            second = asyncio.create_task(dispatcher.feed_update(self.bot, Update(update_id=2, message=self.message("ABC"))))
        finally:
            release.set()
        await first
        await second
        self.create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
