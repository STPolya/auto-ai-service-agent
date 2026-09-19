"""Reusable booking validation, independent of Telegram and database sessions."""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BOOKING_TIMEZONE = ZoneInfo("Europe/Amsterdam")


class BookingValidationError(ValueError):
    """A safe, user-facing explanation of an invalid booking."""


def local_now() -> datetime:
    return datetime.now(BOOKING_TIMEZONE)


def validate_date(value: date, *, now: datetime | None = None) -> date:
    today = (now or local_now()).astimezone(BOOKING_TIMEZONE).date()
    if not today <= value <= today + timedelta(days=90):
        raise BookingValidationError("Choose a date from today through the next 90 days.")
    return value


def parse_date(value: str, *, now: datetime | None = None) -> date:
    if not re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", value):
        raise BookingValidationError("Enter a real date in DD.MM.YYYY format.")
    try:
        result = datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        raise BookingValidationError("Enter a real date in DD.MM.YYYY format.") from None
    return validate_date(result, now=now)


def validate_appointment_at(value: datetime, *, now: datetime | None = None) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BookingValidationError("Appointment time must include a timezone.")
    value = value.astimezone(BOOKING_TIMEZONE)
    current = (now or local_now()).astimezone(BOOKING_TIMEZONE)
    validate_date(value.date(), now=current)
    if not (9 <= value.hour < 18 and value.minute in (0, 30) and value.second == 0 and value.microsecond == 0):
        raise BookingValidationError("Choose a start time every 30 minutes from 09:00 through 17:30.")
    if value <= current:
        raise BookingValidationError("That time has already passed. Choose a later time or restart for another date.")
    return value


def parse_time(value: str, day: date, *, now: datetime | None = None) -> datetime:
    if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
        raise BookingValidationError("Enter a time in HH:MM format.")
    try:
        hour, minute = map(int, value.split(":"))
        result = datetime.combine(day, time(hour, minute), tzinfo=BOOKING_TIMEZONE)
    except ValueError:
        raise BookingValidationError("Enter a real time in HH:MM format.") from None
    return validate_appointment_at(result, now=now)
