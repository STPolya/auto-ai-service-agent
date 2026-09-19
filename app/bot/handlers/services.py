"""Telegram presentation of database catalog entries."""

import asyncio
import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import Message

from app.bot.keyboards.main_menu import SERVICES_BUTTON
from app.database.models import Service
from app.services.service_catalog import CatalogError, get_active_services

router = Router(name="services")
logger = logging.getLogger(__name__)
EMPTY_MESSAGE = "No services are available right now. Please try again later."
ERROR_MESSAGE = "Sorry, we can't load services right now. Please try again shortly."


def format_price(price: Decimal | None) -> str:
    return "Price on request" if price is None else f"From €{price:.2f}"


def format_service(service: Service) -> str:
    lines = [f"🔧 {service.name}", format_price(service.price_from),
             f"About {service.duration_minutes} min"]
    if service.description:
        lines.append(service.description)
    return "\n".join(lines)


@router.message(F.text == SERVICES_BUTTON)
async def handle_services(message: Message) -> None:
    try:
        services = await asyncio.to_thread(get_active_services)
    except CatalogError:
        logger.error("Service catalog loading failed; database operation unavailable.")
        await message.answer(ERROR_MESSAGE)
        return
    if not services:
        await message.answer(EMPTY_MESSAGE)
        return
    # Separate entries keep a growing catalog within Telegram's message limit.
    # Long descriptions are split conservatively, including supplementary Unicode.
    for service in services:
        text = format_service(service)
        for offset in range(0, len(text), 2000):
            await message.answer(text[offset:offset + 2000], parse_mode=None)
