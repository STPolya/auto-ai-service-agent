"""Reusable booking validation, independent of Telegram and database sessions."""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BOOKING_TIMEZONE = ZoneInfo("Europe/Moscow")
BOOKING_TIMEZONE_LABEL = "МСК"
START_MINUTES = tuple(range(9 * 60, 18 * 60, 30))


class BookingValidationError(ValueError):
    """A safe, user-facing explanation of an invalid booking."""


def local_now() -> datetime:
    return datetime.now(BOOKING_TIMEZONE)


def validate_date(value: date, *, now: datetime | None = None) -> date:
    today = (now or local_now()).astimezone(BOOKING_TIMEZONE).date()
    if not today <= value <= today + timedelta(days=90):
        raise BookingValidationError("Выберите дату от сегодняшнего дня до 90 дней вперёд.")
    return value


def parse_date(value: str, *, now: datetime | None = None) -> date:
    if not re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", value):
        raise BookingValidationError("Введите существующую дату в формате ДД.ММ.ГГГГ.")
    try:
        result = datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        raise BookingValidationError("Введите существующую дату в формате ДД.ММ.ГГГГ.") from None
    return validate_date(result, now=now)


def validate_appointment_at(value: datetime, *, now: datetime | None = None) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BookingValidationError("Не удалось определить часовой пояс записи. Выберите время заново.")
    value = value.astimezone(BOOKING_TIMEZONE)
    current = (now or local_now()).astimezone(BOOKING_TIMEZONE)
    validate_date(value.date(), now=current)
    if not (value.hour * 60 + value.minute in START_MINUTES and value.second == 0 and value.microsecond == 0):
        raise BookingValidationError("Выберите время с 09:00 до 17:30 с интервалом 30 минут.")
    if value <= current:
        raise BookingValidationError("Это время уже прошло. Выберите другое время или дату.")
    return value


def parse_time(value: str, day: date, *, now: datetime | None = None) -> datetime:
    if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
        raise BookingValidationError("Не удалось распознать время. Выберите вариант на кнопке.")
    try:
        hour, minute = map(int, value.split(":"))
        result = datetime.combine(day, time(hour, minute), tzinfo=BOOKING_TIMEZONE)
    except ValueError:
        raise BookingValidationError("Такого времени нет. Выберите вариант на кнопке.") from None
    return validate_appointment_at(result, now=now)


def selectable_times(day: date, *, now: datetime | None = None) -> list[str]:
    """Business start times, not capacity or mechanic availability."""
    current = now or local_now()
    validate_date(day, now=current)
    return [f"{minutes // 60:02}:{minutes % 60:02}" for minutes in START_MINUTES
            if datetime.combine(day, time(minutes // 60, minutes % 60), tzinfo=BOOKING_TIMEZONE) > current]
