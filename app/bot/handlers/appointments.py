"""Upcoming appointments in the car service's local timezone."""

import asyncio
import logging
from datetime import timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards.main_menu import APPOINTMENTS_BUTTON, main_menu_keyboard
from app.services.appointment_service import AppointmentError, list_upcoming_appointments
from app.services.booking_rules import BOOKING_TIMEZONE

router = Router(name="appointments")
router.message.filter(F.chat.type == "private")
logger = logging.getLogger(__name__)
EMPTY_MESSAGE = "You have no upcoming appointments."
ERROR_MESSAGE = "Sorry, we can't load appointments right now. Please try again shortly."


@router.message(F.text == APPOINTMENTS_BUTTON)
async def show_appointments(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.from_user is None:
        await message.answer("Please open a private chat with this bot and send /start.")
        return
    try:
        appointments = await asyncio.to_thread(list_upcoming_appointments, message.from_user.id)
    except AppointmentError:
        logger.error("Appointment listing failed; database operation unavailable.")
        await message.answer(ERROR_MESSAGE, reply_markup=main_menu_keyboard())
        return
    if not appointments:
        await message.answer(EMPTY_MESSAGE, reply_markup=main_menu_keyboard())
        return
    for item in appointments:
        # SQLite tests return naive UTC; PostgreSQL timestamptz returns aware values.
        when = item.appointment_at
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        when = when.astimezone(BOOKING_TIMEZONE)
        vehicle = item.vehicle
        await message.answer(
            f"🚗 {vehicle.brand} {vehicle.model} ({vehicle.year or 'year not specified'})\n"
            f"Service: {item.service.name}\nDate: {when:%d.%m.%Y}\n"
            f"Time: {when:%H:%M} (Europe/Amsterdam)\nStatus: {item.status}",
            parse_mode=None, reply_markup=main_menu_keyboard(),
        )
