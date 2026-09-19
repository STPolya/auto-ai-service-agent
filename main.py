"""Start the Telegram bot using local long polling."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.utils.token import TokenValidationError

from app.bot.handlers.start import router as start_router
from app.bot.handlers.services import router as services_router
from app.config.settings import get_bot_token


async def main() -> None:
    try:
        token = get_bot_token()
        bot = Bot(token=token)
    except (ValueError, TokenValidationError):
        raise SystemExit(
            "Set TELEGRAM_BOT_TOKEN to a valid bot token in .env "
            "or your environment."
        ) from None

    dispatcher = Dispatcher()
    dispatcher.include_router(start_router)
    dispatcher.include_router(services_router)

    async with bot.context():
        await dispatcher.start_polling(bot, close_bot_session=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
