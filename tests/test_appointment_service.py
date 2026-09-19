"""Appointment transactions use only isolated SQLite, never Supabase."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.models import Appointment, Service, User, Vehicle
from app.services.appointment_service import (
    AppointmentError, create_appointment, get_booking_selection, list_upcoming_appointments,
)
from app.services.booking_rules import BOOKING_TIMEZONE, BookingValidationError

NOW = datetime(2026, 9, 19, 8, tzinfo=BOOKING_TIMEZONE)
WHEN = datetime(2026, 9, 25, 14, 30, tzinfo=BOOKING_TIMEZONE)


class AppointmentServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.factory = sessionmaker(engine, expire_on_commit=False)
        for target, kwargs in (
            ("app.services.appointment_service.get_session_factory", {"return_value": self.factory}),
            ("app.services.appointment_service.local_now", {"return_value": NOW}),
            ("app.services.booking_rules.local_now", {"return_value": NOW}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        with self.factory.begin() as session:
            session.add_all([User(id=1, telegram_id=100), User(id=2, telegram_id=200)])
            session.flush()
            session.add_all([
                Vehicle(id=1, user_id=1, brand="Toyota", model="Corolla", year=2020),
                Vehicle(id=2, user_id=2, brand="Other", model="Private", year=2020),
                Service(id=1, name="Oil change", duration_minutes=60),
                Service(id=2, name="Inactive", duration_minutes=60, is_active=False),
            ])

    def appointments(self):
        with self.factory() as session:
            return list(session.scalars(select(Appointment)))

    def test_selection_is_read_only(self):
        vehicle, service = get_booking_selection(100, 1, 1)
        self.assertEqual((vehicle.id, service.id), (1, 1))
        self.assertEqual(self.appointments(), [])

    def test_confirmation_creates_correct_owned_appointment(self):
        result = create_appointment(100, 1, 1, WHEN)
        self.assertIsNotNone(result.id)
        self.assertEqual((result.user_id, result.vehicle_id, result.service_id, result.status), (1, 1, 1, "scheduled"))
        self.assertIsNone(result.problem_description)
        self.assertEqual(result.appointment_at, WHEN.astimezone(timezone.utc))
        self.assertEqual(len(self.appointments()), 1)

    def test_forged_vehicle_or_missing_user_rejected(self):
        for user, vehicle in ((100, 2), (999, 1), (100, 999)):
            with self.assertRaises(BookingValidationError):
                create_appointment(user, vehicle, 1, WHEN)
        self.assertEqual(self.appointments(), [])

    def test_inactive_and_missing_services_rejected(self):
        for service in (2, 999):
            with self.assertRaises(BookingValidationError):
                create_appointment(100, 1, service, WHEN)
        self.assertEqual(self.appointments(), [])

    def test_service_deactivated_after_review_is_rechecked(self):
        get_booking_selection(100, 1, 1)
        with self.factory.begin() as session:
            session.get(Service, 1).is_active = False
        with self.assertRaises(BookingValidationError):
            create_appointment(100, 1, 1, WHEN)
        self.assertEqual(self.appointments(), [])

    def test_time_revalidated_at_confirmation(self):
        with patch("app.services.booking_rules.local_now", return_value=WHEN):
            with self.assertRaises(BookingValidationError):
                create_appointment(100, 1, 1, WHEN)
        self.assertEqual(self.appointments(), [])

    def test_upcoming_is_owned_chronological_and_relationships_loaded(self):
        with self.factory.begin() as session:
            for id_, user, vehicle, when in (
                (1, 1, 1, datetime(2026, 9, 25, 12, tzinfo=timezone.utc)),
                (2, 2, 2, datetime(2026, 9, 20, 12, tzinfo=timezone.utc)),
                (3, 1, 1, datetime(2026, 9, 18, 12, tzinfo=timezone.utc)),
                (4, 1, 1, datetime(2026, 9, 20, 12, tzinfo=timezone.utc)),
            ):
                session.add(Appointment(id=id_, user_id=user, vehicle_id=vehicle, service_id=1, appointment_at=when))
        upcoming = list_upcoming_appointments(100)
        self.assertEqual([a.id for a in upcoming], [4, 1])
        self.assertEqual(upcoming[0].vehicle.brand, "Toyota")
        self.assertEqual(upcoming[0].service.name, "Oil change")
        self.assertEqual(list_upcoming_appointments(999), [])

    def test_database_failure_rolls_back_and_hides_details(self):
        def fail(session):
            session.flush()
            raise OperationalError(None, None, RuntimeError("private-detail"))
        event.listen(self.factory, "before_commit", fail)
        with self.assertRaises(AppointmentError) as error:
            create_appointment(100, 1, 1, WHEN)
        self.assertNotIn("private-detail", str(error.exception))
        self.assertEqual(self.appointments(), [])
