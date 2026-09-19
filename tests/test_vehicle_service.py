"""Owner-scoped vehicle operations against an isolated in-memory database."""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.models import User, Vehicle
from app.services.user_service import sync_user
from app.services.vehicle_service import VehicleError, create_vehicle, list_vehicles


class VehicleServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.factory = sessionmaker(engine, expire_on_commit=False)
        for target in ("app.services.vehicle_service.get_session_factory",
                       "app.services.user_service.get_session_factory"):
            patcher = patch(target, return_value=self.factory)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_listing_is_owner_scoped_and_ordered(self):
        owner = sync_user(100, "owner", "Owner")
        other = sync_user(200, "other", "Other")
        with self.factory.begin() as session:
            session.add_all([
                Vehicle(id=30, user_id=owner.id, brand="Toyota", model="Last"),
                Vehicle(id=10, user_id=owner.id, brand="Toyota", model="First"),
                Vehicle(id=20, user_id=other.id, brand="Private", model="Other"),
            ])
        self.assertEqual([v.id for v in list_vehicles(100, "owner", "Owner")], [10, 30])
        self.assertEqual([v.id for v in list_vehicles(200, "other", "Other")], [20])

    def test_creation_resolves_owner_from_telegram_id(self):
        sync_user(200, None, None)
        owner = sync_user(9876543210, "driver", "Alex")
        result = create_vehicle(9876543210, "driver", "Alex", brand="Toyota", model="Corolla",
                                year=2020, license_plate="AB-123-CD")
        with self.factory() as session:
            vehicle = session.get(Vehicle, result.id)
            self.assertEqual(vehicle.user_id, owner.id)
            self.assertEqual((vehicle.brand, vehicle.model, vehicle.year, vehicle.license_plate),
                             ("Toyota", "Corolla", 2020, "AB-123-CD"))

    def test_missing_user_on_list_uses_existing_sync(self):
        with patch("app.services.vehicle_service.sync_user", wraps=sync_user) as sync:
            self.assertEqual(list_vehicles(100, None, "Alex"), [])
            sync.assert_called_once_with(100, None, "Alex")
        with self.factory() as session:
            self.assertIsNotNone(session.scalar(select(User).where(User.telegram_id == 100)))

    def test_missing_user_on_create_and_optional_plate(self):
        with patch("app.services.vehicle_service.sync_user", wraps=sync_user) as sync:
            vehicle = create_vehicle(100, None, None, brand="Toyota", model="Corolla",
                                     year=2020, license_plate=None)
            sync.assert_called_once_with(100, None, None)
        self.assertIsNone(vehicle.license_plate)
        self.assertEqual([v.id for v in list_vehicles(100, None, None)], [vehicle.id])

    def test_database_errors_are_sanitized(self):
        with patch("app.services.vehicle_service.sync_user", side_effect=OperationalError(None, None, Exception("private-detail"))):
            with self.assertRaises(VehicleError) as error:
                list_vehicles(100, None, None)
        self.assertNotIn("private-detail", str(error.exception))


if __name__ == "__main__":
    unittest.main()
