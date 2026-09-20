"""Start the Telegram bot using local long polling."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.utils.token import TokenValidationError
from aiogram.fsm.storage.memory import SimpleEventIsolation

from app.bot.handlers.start import router as start_router
from app.bot.handlers.services import router as services_router
from app.bot.handlers.vehicles import router as vehicles_router
from app.bot.handlers.booking import router as booking_router
from app.bot.handlers.appointments import router as appointments_router
from app.bot.handlers.diagnostics import router as diagnostics_router
from app.bot.handlers.handoff import router as handoff_router
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

    dispatcher = Dispatcher(events_isolation=SimpleEventIsolation())
    dispatcher.include_router(diagnostics_router)
    dispatcher.include_router(handoff_router)
    dispatcher.include_router(appointments_router)
    dispatcher.include_router(booking_router)
    dispatcher.include_router(vehicles_router)
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
