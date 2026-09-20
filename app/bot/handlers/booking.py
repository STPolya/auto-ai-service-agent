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
from app.bot.keyboards.booking import choices_keyboard, confirmation_keyboard, times_keyboard
from app.bot.presentation import service_name
from app.bot.keyboards.main_menu import BOOKING_BUTTON, main_menu_keyboard
from app.bot.keyboards.vehicles import add_vehicle_keyboard
from app.bot.states.booking import Booking
from app.services.appointment_service import AppointmentError, create_appointment, get_booking_selection
from app.services.booking_rules import BOOKING_TIMEZONE_LABEL, BookingValidationError, parse_date, parse_time, selectable_times
from app.services.service_catalog import CatalogError, get_active_services
from app.services.vehicle_service import VehicleError, list_vehicles

router = Router(name="booking")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
logger = logging.getLogger(__name__)
ERROR_MESSAGE = "Сервис записи временно недоступен. Попробуйте позже."
SAVE_ERROR_MESSAGE = "Не удалось подтвердить сохранение. Проверьте раздел «📋 Мои записи», прежде чем повторить попытку."
STALE_MESSAGE = "Эта кнопка устарела. Начните запись заново."
DATE_PROMPT = f"Введите дату в формате ДД.ММ.ГГГГ: начиная с сегодняшнего дня и не более чем на 90 дней вперёд ({BOOKING_TIMEZONE_LABEL})."
TIME_PROMPT = f"Выберите удобное время ({BOOKING_TIMEZONE_LABEL}):"
NO_TIMES_MESSAGE = "На сегодня доступного времени больше нет. Пожалуйста, выберите другую дату."
SUCCESS_MESSAGE = "Готово! Запись создана ✅\n\nНаш менеджер свяжется с вами для подтверждения записи и уточнения деталей."


def vehicle_label(vehicle) -> str:
    return f"{vehicle.brand} {vehicle.model} ({vehicle.year or 'год не указан'})"


async def abort_with_error(message: Message, state: FSMContext) -> None:
    await state.clear()
    logger.error("Booking operation failed; database operation unavailable.")
    await message.answer(ERROR_MESSAGE, reply_markup=main_menu_keyboard())


@router.message(Command("cancel"), StateFilter(Booking))
async def cancel_booking(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Запись отменена.", reply_markup=main_menu_keyboard())


@router.message(F.text == BOOKING_BUTTON)
async def begin_booking(message: Message, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    if user is None:
        await message.answer("Откройте личный чат с ботом и отправьте /start.")
        return
    try:
        vehicles = await asyncio.to_thread(list_vehicles, user.id, user.username, user.first_name)
    except VehicleError:
        await abort_with_error(message, state)
        return
    if not vehicles:
        await message.answer("Чтобы записаться на сервис, сначала добавьте автомобиль.", reply_markup=add_vehicle_keyboard())
        return
    token = token_hex(6)
    await state.set_data({"token": token})
    await state.set_state(Booking.vehicle)
    await message.answer("Выберите автомобиль. Для отмены в любой момент отправьте /cancel.", reply_markup=choices_keyboard(
        token, "vehicle", [(v.id, vehicle_label(v)) for v in vehicles],
    ))


async def choose_vehicle(callback: CallbackQuery, state: FSMContext, identifier: int, data: dict) -> None:
    user = callback.from_user
    vehicles = await asyncio.to_thread(list_vehicles, user.id, user.username, user.first_name)
    vehicle = next((v for v in vehicles if v.id == identifier), None)
    if vehicle is None:
        raise BookingValidationError("Этот автомобиль недоступен. Начните запись заново.")
    services = await asyncio.to_thread(get_active_services)
    if not services:
        await state.clear()
        await callback.message.answer("Сейчас нет доступных услуг. Пожалуйста, попробуйте позже.",
                                      reply_markup=main_menu_keyboard())
        return
    await state.update_data(vehicle_id=identifier)
    await state.set_state(Booking.service)
    await callback.message.answer("Выберите услугу:", reply_markup=choices_keyboard(
        data["token"], "service", [(s.id, f"{service_name(s.name)} — {format_price(s.price_from)}") for s in services],
    ))


async def choose_service(callback: CallbackQuery, state: FSMContext, identifier: int, data: dict) -> None:
    await asyncio.to_thread(get_booking_selection, callback.from_user.id, data["vehicle_id"], identifier)
    await state.update_data(service_id=identifier)
    await state.set_state(Booking.date)
    await callback.message.answer(DATE_PROMPT)


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
    await callback.message.answer(SUCCESS_MESSAGE + "\n\n" + data["summary"],
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
                "time": Booking.time.state, "date": Booking.time.state,
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
        elif action == "time":
            await choose_time(callback, state, identifier, data)
        elif action == "date":
            await state.set_state(Booking.date)
            await callback.message.answer(DATE_PROMPT)
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
    # Rotate the token so time buttons from an earlier date cannot select a new date.
    await state.update_data(date=day.isoformat(), token=token_hex(6))
    await show_times(message, state)


async def show_times(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        times = selectable_times(date.fromisoformat(data["date"]))
    except BookingValidationError as error:
        await state.set_state(Booking.date)
        await message.answer(str(error) + "\n" + DATE_PROMPT)
        return
    if not times:
        await state.set_state(Booking.date)
        await message.answer(NO_TIMES_MESSAGE + "\n" + DATE_PROMPT)
        return
    await state.set_state(Booking.time)
    await message.answer(TIME_PROMPT, reply_markup=times_keyboard(data["token"], times))


@router.message(Booking.time)
async def receive_time(message: Message, state: FSMContext) -> None:
    # Time entry is button-only; validation still happens on the server.
    await show_times(message, state)


async def choose_time(callback: CallbackQuery, state: FSMContext, minutes: int, data: dict) -> None:
    try:
        when = parse_time(f"{minutes // 60:02}:{minutes % 60:02}", date.fromisoformat(data["date"]))
    except BookingValidationError as error:
        await callback.message.answer(str(error))
        await show_times(callback.message, state)
        return
    vehicle, service = await asyncio.to_thread(get_booking_selection, callback.from_user.id,
                                              data["vehicle_id"], data["service_id"])
    summary = (f"Запись на сервис\n\nАвтомобиль: {vehicle_label(vehicle)}\nУслуга: {service_name(service.name)}\n"
               f"Цена: {format_price(service.price_from)}\nДата: {when:%d.%m.%Y}\n"
               f"Время: {when:%H:%M} ({BOOKING_TIMEZONE_LABEL})")
    await state.update_data(appointment_at=when.isoformat(), summary=summary)
    await state.set_state(Booking.confirmation)
    await callback.message.answer(summary,
                         parse_mode=None, reply_markup=confirmation_keyboard(data["token"]))


@router.message(StateFilter(Booking))
async def booking_help(message: Message) -> None:
    await message.answer("Используйте кнопки записи или отправьте /cancel для отмены.")
