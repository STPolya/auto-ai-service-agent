import asyncio
import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.keyboards.main_menu import main_menu_keyboard
from app.services.user_service import UserSyncError, sync_user


router = Router(name="start")
logger = logging.getLogger(__name__)

SYNC_ERROR_MESSAGE = "Sorry, we're having a temporary technical problem. Please try /start again shortly."
MISSING_USER_MESSAGE = "Please open a private chat with this bot and send /start."

WELCOME_MESSAGE = (
    "Welcome to AutoCare! 🚗\n\n"
    "I'm your car service AI assistant in development. Soon I'll help you "
    "explore possible causes of car problems, learn about our services "
    "and prices, and make service appointments.\n\n"
    "This is our first prototype: the menu below is a preview, "
    "and its features are coming soon."
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

    await message.answer(WELCOME_MESSAGE, reply_markup=main_menu_keyboard())
