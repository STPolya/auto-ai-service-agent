import unittest
from datetime import date, datetime, timedelta, timezone

from app.services.booking_rules import (
    BOOKING_TIMEZONE, BookingValidationError, parse_date, parse_time, validate_appointment_at,
)

NOW = datetime(2026, 9, 19, 10, 10, tzinfo=BOOKING_TIMEZONE)


class BookingRulesTests(unittest.TestCase):
    def test_valid_date_and_ninety_day_boundary(self):
        self.assertEqual(parse_date("19.09.2026", now=NOW), date(2026, 9, 19))
        boundary = NOW.date() + timedelta(days=90)
        self.assertEqual(parse_date(boundary.strftime("%d.%m.%Y"), now=NOW), boundary)

    def test_invalid_dates(self):
        for text in ("31.09.2026", "29.02.2026", "2026-09-25", "1.10.2026", " 25.09.2026", ""):
            with self.subTest(text=text), self.assertRaises(BookingValidationError):
                parse_date(text, now=NOW)

    def test_past_and_beyond_ninety_days(self):
        for day in (NOW.date() - timedelta(days=1), NOW.date() + timedelta(days=91)):
            with self.assertRaises(BookingValidationError):
                parse_date(day.strftime("%d.%m.%Y"), now=NOW)

    def test_valid_half_hour_times(self):
        for text in ("09:00", "09:30", "14:00", "17:30"):
            self.assertEqual(parse_time(text, date(2026, 9, 20), now=NOW).strftime("%H:%M"), text)

    def test_invalid_times(self):
        for text in ("08:30", "18:00", "14:15", "24:00", "09:60", "9:00", " 09:00", ""):
            with self.subTest(text=text), self.assertRaises(BookingValidationError):
                parse_time(text, date(2026, 9, 20), now=NOW)

    def test_past_time_today(self):
        with self.assertRaises(BookingValidationError):
            parse_time("10:00", NOW.date(), now=NOW)
        self.assertEqual(parse_time("10:30", NOW.date(), now=NOW).hour, 10)

    def test_amsterdam_date_differs_from_utc_near_midnight(self):
        now = datetime(2026, 9, 19, 22, 30, tzinfo=timezone.utc)
        with self.assertRaises(BookingValidationError):
            parse_date("19.09.2026", now=now)
        self.assertEqual(parse_date("20.09.2026", now=now), date(2026, 9, 20))

    def test_winter_offset_and_naive_time_rejected(self):
        value = parse_time("09:00", date(2026, 11, 1), now=NOW)
        self.assertEqual(value.utcoffset(), timedelta(hours=1))
        with self.assertRaises(BookingValidationError):
            validate_appointment_at(datetime(2026, 9, 20, 9), now=NOW)
