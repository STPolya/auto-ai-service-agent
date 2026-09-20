"""Telegram presentation of database catalog entries."""

import asyncio
import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import Message

from app.bot.keyboards.main_menu import SERVICES_BUTTON
from app.bot.presentation import service_name, service_description
from app.database.models import Service
from app.services.service_catalog import CatalogError, get_active_services

router = Router(name="services")
logger = logging.getLogger(__name__)
EMPTY_MESSAGE = "Сейчас нет доступных услуг. Пожалуйста, попробуйте позже."
ERROR_MESSAGE = "Не удалось загрузить услуги. Попробуйте позже."


def format_price(price: Decimal | None) -> str:
    return "Цена по запросу" if price is None else f"от €{price:.2f}"


def format_service(service: Service) -> str:
    lines = [f"🔧 {service_name(service.name)}", format_price(service.price_from),
             f"Примерно {service.duration_minutes} мин"]
    if service.description:
        lines.append(service_description(service.description))
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
