from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.keyboards.main_menu import main_menu_keyboard


router = Router(name="start")

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
    await message.answer(WELCOME_MESSAGE, reply_markup=main_menu_keyboard())
