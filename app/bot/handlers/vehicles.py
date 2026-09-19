"""Private-chat vehicle listing and a temporary add-vehicle conversation."""

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.bot.keyboards.main_menu import MENU_LABELS, VEHICLES_BUTTON, main_menu_keyboard
from app.bot.keyboards.vehicles import ADD_VEHICLE_CALLBACK, add_vehicle_keyboard
from app.bot.states.vehicle import AddVehicle
from app.database.models import Vehicle
from app.services.vehicle_service import VehicleError, create_vehicle, list_vehicles

router = Router(name="vehicles")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
logger = logging.getLogger(__name__)
EMPTY_MESSAGE = "You haven't added any vehicles yet."
ERROR_MESSAGE = "Sorry, we're having a temporary technical problem. Please try again shortly."
SAVE_ERROR_MESSAGE = (
    "Sorry, we couldn't confirm that your vehicle was saved. "
    "Please check My vehicles before starting again."
)
BRAND_PROMPT = "What is your vehicle's brand? Send /cancel at any time to cancel."
MODEL_PROMPT = "What is the vehicle model?"
YEAR_PROMPT = "What year is the vehicle?"
PLATE_PROMPT = "What is the license plate? Send /skip to leave it unspecified."


def format_vehicle(vehicle: Vehicle) -> str:
    year = str(vehicle.year) if vehicle.year is not None else "year not specified"
    return f"🚗 {vehicle.brand} {vehicle.model} ({year})\nPlate: {vehicle.license_plate or 'not specified'}"


def profile(message: Message) -> dict:
    user = message.from_user
    return {"telegram_id": user.id, "username": user.username, "first_name": user.first_name}


def normalized_text(message: Message) -> str:
    text = (message.text or "").strip()
    # Commands and old reply-keyboard buttons must never become vehicle data.
    return "" if text.startswith("/") or text in MENU_LABELS else text


@router.message(Command("cancel"))
async def cancel_vehicle(message: Message, state: FSMContext) -> None:
    active = await state.get_state()
    await state.clear()
    await message.answer("Vehicle entry cancelled." if active else "No vehicle entry is in progress.",
                         reply_markup=main_menu_keyboard())


@router.message(F.text == VEHICLES_BUTTON)
async def show_vehicles(message: Message, state: FSMContext) -> None:
    # Opening the list deliberately abandons an unfinished form.
    await state.clear()
    if message.from_user is None:
        await message.answer("Please open a private chat with this bot and send /start.")
        return
    try:
        vehicles = await asyncio.to_thread(list_vehicles, **profile(message))
    except VehicleError:
        logger.error("Vehicle listing failed; database operation unavailable.")
        await message.answer(ERROR_MESSAGE, reply_markup=main_menu_keyboard())
        return
    if not vehicles:
        await message.answer(EMPTY_MESSAGE, reply_markup=add_vehicle_keyboard())
        return
    for vehicle in vehicles:
        await message.answer(format_vehicle(vehicle), parse_mode=None)
    await message.answer("Add another vehicle:", reply_markup=add_vehicle_keyboard())


@router.callback_query(F.data == ADD_VEHICLE_CALLBACK)
async def begin_vehicle(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    if await state.get_state() is not None:
        await callback.message.answer("A vehicle entry is already in progress. Continue or send /cancel.")
        return
    await state.set_state(AddVehicle.brand)
    await callback.message.answer(BRAND_PROMPT, reply_markup=ReplyKeyboardRemove())


@router.message(AddVehicle.brand)
async def receive_brand(message: Message, state: FSMContext) -> None:
    value = normalized_text(message)
    if not 1 <= len(value) <= 100:
        await message.answer("Enter a brand using 1–100 characters, or send /cancel.")
        return
    await state.update_data(brand=value)
    await state.set_state(AddVehicle.model)
    await message.answer(MODEL_PROMPT)


@router.message(AddVehicle.model)
async def receive_model(message: Message, state: FSMContext) -> None:
    value = normalized_text(message)
    if not 1 <= len(value) <= 100:
        await message.answer("Enter a model using 1–100 characters, or send /cancel.")
        return
    await state.update_data(model=value)
    await state.set_state(AddVehicle.year)
    await message.answer(YEAR_PROMPT)


@router.message(AddVehicle.year)
async def receive_year(message: Message, state: FSMContext) -> None:
    value = normalized_text(message)
    maximum = datetime.now(timezone.utc).year + 1
    if not value.isascii() or not value.isdigit() or len(value) != 4 or not 1886 <= int(value) <= maximum:
        await message.answer(f"Enter a whole-number year from 1886 to {maximum}, or send /cancel.")
        return
    await state.update_data(year=int(value))
    await state.set_state(AddVehicle.license_plate)
    await message.answer(PLATE_PROMPT)


async def save_vehicle(message: Message, state: FSMContext, plate: str | None) -> None:
    data = await state.get_data()
    # Clear before saving: no automatic retry after ambiguous commit/network errors.
    # Dispatcher event isolation serializes updates so a queued reply cannot save twice.
    await state.clear()
    if message.from_user is None:
        await message.answer("Please send /start and try adding your vehicle again.", reply_markup=main_menu_keyboard())
        return
    try:
        vehicle = await asyncio.to_thread(create_vehicle, **profile(message), **data, license_plate=plate)
    except VehicleError:
        logger.error("Vehicle saving failed; database operation unavailable.")
        await message.answer(SAVE_ERROR_MESSAGE, reply_markup=main_menu_keyboard())
        return
    await message.answer("Vehicle saved!\n\n" + format_vehicle(vehicle),
                         reply_markup=main_menu_keyboard(), parse_mode=None)


@router.message(AddVehicle.license_plate, Command("skip"))
async def skip_plate(message: Message, state: FSMContext) -> None:
    await save_vehicle(message, state, None)


@router.message(AddVehicle.license_plate)
async def receive_plate(message: Message, state: FSMContext) -> None:
    value = normalized_text(message)
    if not 1 <= len(value) <= 64:
        await message.answer("Enter a plate using 1–64 characters, or send /skip to omit it.")
        return
    await save_vehicle(message, state, value)
