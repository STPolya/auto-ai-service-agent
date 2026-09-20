import asyncio
import logging
from pathlib import Path

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, Message

from app.bot.keyboards.main_menu import main_menu_keyboard
from app.services.user_service import UserSyncError, sync_user


router = Router(name="start")
logger = logging.getLogger(__name__)

SYNC_ERROR_MESSAGE = "Сервис временно недоступен. Попробуйте отправить /start позже."
MISSING_USER_MESSAGE = "Откройте личный чат с ботом и отправьте /start."
WELCOME_IMAGE_PATH = Path(__file__).resolve().parents[3] / "assets" / "welcome.png"

WELCOME_MESSAGE = (
    "Добро пожаловать в AutoCare! 🚗\n\n"
    "Я — AI-помощник автосервиса. Помогу разобраться в возможных причинах неисправности "
    "автомобиля, расскажу об услугах и ценах, а также помогу оформить запись на обслуживание.\n\n"
    "Выберите нужный раздел в меню ниже 👇"
)


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        await message.answer(MISSING_USER_MESSAGE)
        return

    try:
        await asyncio.to_thread(
            sync_user,
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
        )
    except UserSyncError:
        # Raw exceptions/tracebacks may expose driver connection details.
        logger.error("Telegram user synchronization failed; database operation unavailable.")
        await message.answer(SYNC_ERROR_MESSAGE)
        return

    if WELCOME_IMAGE_PATH.is_file():
        await message.answer_photo(FSInputFile(WELCOME_IMAGE_PATH), caption=WELCOME_MESSAGE,
                                   reply_markup=main_menu_keyboard())
    else:
        await message.answer(WELCOME_MESSAGE, reply_markup=main_menu_keyboard())
