"""Offline catalog and seed tests using an isolated in-memory database."""

import unittest
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.models import Service
from app.services.service_catalog import CatalogError, get_active_services
from scripts.seed_services import INITIAL_SERVICES, SeedError, seed_services


class CatalogTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.factory = sessionmaker(engine, expire_on_commit=False)
        for target in ("app.services.service_catalog.get_session_factory", "scripts.seed_services.get_session_factory"):
            patcher = patch(target, return_value=self.factory)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_active_only_in_id_order(self):
        with self.factory.begin() as session:
            session.add_all([
                Service(id=30, name="Last", duration_minutes=30),
                Service(id=10, name="First", duration_minutes=60),
                Service(id=20, name="Hidden", duration_minutes=45, is_active=False),
            ])
        services = get_active_services()
        self.assertEqual([s.name for s in services], ["First", "Last"])

    def test_empty_catalog(self):
        self.assertEqual(get_active_services(), [])

    def test_database_error_is_sanitized(self):
        with patch("app.services.service_catalog.get_session_factory", side_effect=OperationalError(None, None, Exception("private-detail"))):
            with self.assertRaises(CatalogError) as error:
                get_active_services()
        self.assertNotIn("private-detail", str(error.exception))

    def test_seed_creates_exact_initial_data(self):
        seed_services()
        services = get_active_services()
        self.assertEqual([(s.name, s.description, s.price_from, s.duration_minutes) for s in services], list(INITIAL_SERVICES))

    def test_seed_twice_preserves_rows_and_edits(self):
        seed_services()
        original_ids = [s.id for s in get_active_services()]
        with self.factory.begin() as session:
            service = session.get(Service, original_ids[0])
            service.price_from = Decimal("99.00")
            service.is_active = False
        seed_services()
        with self.factory() as session:
            services = list(session.scalars(select(Service).order_by(Service.id)))
            self.assertEqual([s.id for s in services], original_ids)
            self.assertEqual(services[0].price_from, Decimal("99.00"))
            self.assertFalse(services[0].is_active)

    def test_seed_failure_is_sanitized(self):
        with patch("scripts.seed_services.get_session_factory", side_effect=ValueError("private-detail")):
            with self.assertRaises(SeedError) as error:
                seed_services()
        self.assertNotIn("private-detail", str(error.exception))


if __name__ == "__main__":
    unittest.main()
