"""Booking draft FSM; persistence happens only after an explicit confirmation."""

import asyncio
import logging
from datetime import date, datetime
from secrets import token_hex

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.handlers.services import format_price
from app.bot.keyboards.booking import choices_keyboard, confirmation_keyboard
from app.bot.keyboards.main_menu import BOOKING_BUTTON, main_menu_keyboard
from app.bot.keyboards.vehicles import add_vehicle_keyboard
from app.bot.states.booking import Booking
from app.services.appointment_service import AppointmentError, create_appointment, get_booking_selection
from app.services.booking_rules import BookingValidationError, parse_date, parse_time
from app.services.service_catalog import CatalogError, get_active_services
from app.services.vehicle_service import VehicleError, list_vehicles

router = Router(name="booking")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
logger = logging.getLogger(__name__)
ERROR_MESSAGE = "Sorry, booking is temporarily unavailable. Please try again shortly."
SAVE_ERROR_MESSAGE = "Sorry, we couldn't confirm the save. Please check My appointments before trying again."
STALE_MESSAGE = "This booking button is no longer valid. Please restart booking."


def vehicle_label(vehicle) -> str:
    return f"{vehicle.brand} {vehicle.model} ({vehicle.year or 'year not specified'})"


async def abort_with_error(message: Message, state: FSMContext) -> None:
    await state.clear()
    logger.error("Booking operation failed; database operation unavailable.")
    await message.answer(ERROR_MESSAGE, reply_markup=main_menu_keyboard())


@router.message(Command("cancel"), StateFilter(Booking))
async def cancel_booking(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Booking cancelled.", reply_markup=main_menu_keyboard())


@router.message(F.text == BOOKING_BUTTON)
async def begin_booking(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    if user is None:
        await message.answer("Please open a private chat with this bot and send /start.")
        return
    try:
        vehicles = await asyncio.to_thread(list_vehicles, user.id, user.username, user.first_name)
    except VehicleError:
        await abort_with_error(message, state)
        return
    if not vehicles:
        await message.answer("Please add a vehicle before booking a service.", reply_markup=add_vehicle_keyboard())
        return
    token = token_hex(6)
    await state.set_data({"token": token})
    await state.set_state(Booking.vehicle)
    await message.answer("Choose your vehicle. Send /cancel at any time.", reply_markup=choices_keyboard(
        token, "vehicle", [(v.id, vehicle_label(v)) for v in vehicles],
    ))


async def choose_vehicle(callback: CallbackQuery, state: FSMContext, identifier: int, data: dict) -> None:
    user = callback.from_user
    vehicles = await asyncio.to_thread(list_vehicles, user.id, user.username, user.first_name)
    vehicle = next((v for v in vehicles if v.id == identifier), None)
    if vehicle is None:
        raise BookingValidationError("That vehicle is unavailable. Please restart booking.")
    services = await asyncio.to_thread(get_active_services)
    if not services:
        await state.clear()
        await callback.message.answer("No active services are available right now. Please try again later.",
                                      reply_markup=main_menu_keyboard())
        return
    await state.update_data(vehicle_id=identifier)
    await state.set_state(Booking.service)
    await callback.message.answer("Choose a service:", reply_markup=choices_keyboard(
        data["token"], "service", [(s.id, f"{s.name} — {format_price(s.price_from)}") for s in services],
    ))


async def choose_service(callback: CallbackQuery, state: FSMContext, identifier: int, data: dict) -> None:
    await asyncio.to_thread(get_booking_selection, callback.from_user.id, data["vehicle_id"], identifier)
    await state.update_data(service_id=identifier)
    await state.set_state(Booking.date)
    await callback.message.answer("Enter a date as DD.MM.YYYY, from today through the next 90 days (Europe/Amsterdam).")


async def confirm_booking(callback: CallbackQuery, state: FSMContext, data: dict) -> None:
    # Clear before I/O. SimpleEventIsolation serializes same-user callbacks.
    # This guards one in-memory draft, not distributed/restart-safe idempotency.
    await state.clear()
    try:
        await asyncio.to_thread(create_appointment, callback.from_user.id, data["vehicle_id"],
                                data["service_id"], datetime.fromisoformat(data["appointment_at"]))
    except AppointmentError:
        logger.error("Appointment saving failed; database operation unavailable.")
        await callback.message.answer(SAVE_ERROR_MESSAGE, reply_markup=main_menu_keyboard())
        return
    await callback.message.answer("Booking saved with status scheduled.\n\n" + data["summary"],
                                  parse_mode=None, reply_markup=main_menu_keyboard())


@router.callback_query(F.data.startswith("book:"))
async def booking_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer(STALE_MESSAGE)
        return
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    current = await state.get_state()
    expected = {"vehicle": Booking.vehicle.state, "service": Booking.service.state,
                "confirm": Booking.confirmation.state, "cancel": Booking.confirmation.state}
    if (len(parts) != 4 or parts[1] != data.get("token") or
            expected.get(parts[2]) != current or current is None or
            not parts[3].isascii() or not parts[3].isdigit() or len(parts[3]) > 10):
        await callback.answer(STALE_MESSAGE)
        return
    await callback.answer()
    action, identifier = parts[2], int(parts[3])
    try:
        if action == "vehicle":
            await choose_vehicle(callback, state, identifier, data)
        elif action == "service":
            await choose_service(callback, state, identifier, data)
        elif action == "cancel":
            await cancel_booking(callback.message, state)
        elif action == "confirm":
            await confirm_booking(callback, state, data)
    except BookingValidationError as error:
        await state.clear()
        await callback.message.answer(str(error), reply_markup=main_menu_keyboard())
    except (VehicleError, CatalogError, AppointmentError):
        await abort_with_error(callback.message, state)


@router.message(Booking.date)
async def receive_date(message: Message, state: FSMContext) -> None:
    try:
        day = parse_date(message.text or "")
    except BookingValidationError as error:
        await message.answer(str(error))
        return
    await state.update_data(date=day.isoformat())
    await state.set_state(Booking.time)
    await message.answer("Enter HH:MM, every 30 minutes from 09:00 to 17:30 (Europe/Amsterdam).")


@router.message(Booking.time)
async def receive_time(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        when = parse_time(message.text or "", date.fromisoformat(data["date"]))
    except BookingValidationError as error:
        await message.answer(str(error) + " Send /cancel to choose another date.")
        return
    if message.from_user is None:
        await state.clear()
        await message.answer("Please send /start and try booking again.")
        return
    try:
        vehicle, service = await asyncio.to_thread(get_booking_selection, message.from_user.id,
                                                  data["vehicle_id"], data["service_id"])
    except BookingValidationError as error:
        await state.clear()
        await message.answer(str(error), reply_markup=main_menu_keyboard())
        return
    except AppointmentError:
        await abort_with_error(message, state)
        return
    summary = (f"Vehicle: {vehicle_label(vehicle)}\nService: {service.name}\n"
               f"Price: {format_price(service.price_from)}\nDate: {when:%d.%m.%Y}\n"
               f"Time: {when:%H:%M} (Europe/Amsterdam)")
    await state.update_data(appointment_at=when.isoformat(), summary=summary)
    await state.set_state(Booking.confirmation)
    await message.answer(summary + "\n\nThis MVP does not check real capacity or guarantee a free slot.",
                         parse_mode=None, reply_markup=confirmation_keyboard(data["token"]))


@router.message(StateFilter(Booking))
async def booking_help(message: Message) -> None:
    await message.answer("Use the booking buttons, or send /cancel to leave this booking.")
