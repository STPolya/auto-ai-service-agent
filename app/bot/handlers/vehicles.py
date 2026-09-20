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
EMPTY_MESSAGE = "У вас пока нет добавленных автомобилей."
ERROR_MESSAGE = "Сервис временно недоступен. Попробуйте позже."
SAVE_ERROR_MESSAGE = (
    "Не удалось подтвердить сохранение автомобиля. "
    "Проверьте раздел «🚗 Мои автомобили», прежде чем повторить попытку."
)
BRAND_PROMPT = "Введите марку автомобиля. Для отмены в любой момент отправьте /cancel."
MODEL_PROMPT = "Введите модель автомобиля."
YEAR_PROMPT = "Введите год выпуска автомобиля."
PLATE_PROMPT = "Введите госномер автомобиля или отправьте /skip, чтобы пропустить."


def format_vehicle(vehicle: Vehicle) -> str:
    year = str(vehicle.year) if vehicle.year is not None else "год не указан"
    return f"🚗 {vehicle.brand} {vehicle.model} ({year})\nГосномер: {vehicle.license_plate or 'не указан'}"


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
    await message.answer("Добавление автомобиля отменено." if active else "Сейчас нечего отменять.",
                         reply_markup=main_menu_keyboard())


@router.message(F.text == VEHICLES_BUTTON)
async def show_vehicles(message: Message, state: FSMContext) -> None:
    # Opening the list deliberately abandons an unfinished form.
    await state.clear()
    if message.from_user is None:
        await message.answer("Откройте личный чат с ботом и отправьте /start.")
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
    await message.answer("Вы можете добавить ещё один автомобиль:", reply_markup=add_vehicle_keyboard())


@router.callback_query(F.data == ADD_VEHICLE_CALLBACK)
async def begin_vehicle(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    if await state.get_state() is not None:
        await callback.message.answer("Вы уже заполняете данные. Продолжите или отправьте /cancel для отмены.")
        return
    await state.set_state(AddVehicle.brand)
    await callback.message.answer(BRAND_PROMPT, reply_markup=ReplyKeyboardRemove())


@router.message(AddVehicle.brand)
async def receive_brand(message: Message, state: FSMContext) -> None:
    value = normalized_text(message)
    if not 1 <= len(value) <= 100:
        await message.answer("Введите марку длиной от 1 до 100 символов или отправьте /cancel.")
        return
    await state.update_data(brand=value)
    await state.set_state(AddVehicle.model)
    await message.answer(MODEL_PROMPT)


@router.message(AddVehicle.model)
async def receive_model(message: Message, state: FSMContext) -> None:
    value = normalized_text(message)
    if not 1 <= len(value) <= 100:
        await message.answer("Введите модель длиной от 1 до 100 символов или отправьте /cancel.")
        return
    await state.update_data(model=value)
    await state.set_state(AddVehicle.year)
    await message.answer(YEAR_PROMPT)


@router.message(AddVehicle.year)
async def receive_year(message: Message, state: FSMContext) -> None:
    value = normalized_text(message)
    maximum = datetime.now(timezone.utc).year + 1
    if not value.isascii() or not value.isdigit() or len(value) != 4 or not 1886 <= int(value) <= maximum:
        await message.answer(f"Введите год целым числом от 1886 до {maximum} или отправьте /cancel.")
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
        await message.answer("Отправьте /start и попробуйте добавить автомобиль снова.", reply_markup=main_menu_keyboard())
        return
    try:
        vehicle = await asyncio.to_thread(create_vehicle, **profile(message), **data, license_plate=plate)
    except VehicleError:
        logger.error("Vehicle saving failed; database operation unavailable.")
        await message.answer(SAVE_ERROR_MESSAGE, reply_markup=main_menu_keyboard())
        return
    await message.answer("Автомобиль добавлен ✅\n\n" + format_vehicle(vehicle),
                         reply_markup=main_menu_keyboard(), parse_mode=None)


@router.message(AddVehicle.license_plate, Command("skip"))
async def skip_plate(message: Message, state: FSMContext) -> None:
    await save_vehicle(message, state, None)


@router.message(AddVehicle.license_plate)
async def receive_plate(message: Message, state: FSMContext) -> None:
    value = normalized_text(message)
    if not 1 <= len(value) <= 64:
        await message.answer("Введите госномер длиной от 1 до 64 символов или отправьте /skip, чтобы пропустить.")
        return
    await save_vehicle(message, state, value)
