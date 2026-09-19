"""Run manually with python -m scripts.seed_services after reviewing the data."""

from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.database.models import Service
from app.database.session import get_session_factory

INITIAL_SERVICES = (
    ("Oil change", "Engine oil and oil filter replacement.", Decimal("79.00"), 60),
    ("Brake inspection", "Inspection of brake pads, discs, and braking system.", Decimal("49.00"), 45),
    ("Computer diagnostics", "Electronic diagnostics and fault-code scan.", Decimal("59.00"), 45),
    ("Tire change", "Seasonal tire replacement for one vehicle.", Decimal("69.00"), 60),
    ("Air conditioning service", "Air-conditioning system inspection and service.", Decimal("89.00"), 60),
)


class SeedError(RuntimeError):
    """Safe error for the manual seed command."""


def seed_services() -> None:
    """Insert missing names atomically; preserve existing edits and active flags."""
    try:
        with get_session_factory().begin() as session:
            for name, description, price, duration in INITIAL_SERVICES:
                session.execute(insert(Service).values(
                    name=name, description=description, price_from=price,
                    duration_minutes=duration, is_active=True,
                ).on_conflict_do_nothing(index_elements=[Service.name]))
    except (SQLAlchemyError, ValueError):
        raise SeedError("Service seed failed. Check database configuration and connectivity.") from None


def main() -> None:
    try:
        seed_services()
    except SeedError:
        raise SystemExit("Service seed failed. Check database configuration and connectivity.") from None
    print("Service seed complete; existing services were preserved.")


if __name__ == "__main__":
    main()
