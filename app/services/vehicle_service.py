"""Owner-scoped vehicle operations, with one session per transaction."""

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database.models import User, Vehicle
from app.database.session import get_session_factory
from app.services.user_service import UserSyncError, sync_user


class VehicleError(RuntimeError):
    """Safe failure for vehicle operation callers."""


def list_vehicles(telegram_id: int, username: str | None, first_name: str | None) -> list[Vehicle]:
    try:
        sync_user(telegram_id, username, first_name)
        with get_session_factory()() as session:
            return list(session.scalars(
                select(Vehicle).join(User).where(User.telegram_id == telegram_id).order_by(Vehicle.id)
            ))
    except (SQLAlchemyError, ValueError, UserSyncError):
        raise VehicleError("Vehicle operation is temporarily unavailable.") from None


def create_vehicle(
    telegram_id: int, username: str | None, first_name: str | None,
    *, brand: str, model: str, year: int, license_plate: str | None,
) -> Vehicle:
    try:
        sync_user(telegram_id, username, first_name)
        with get_session_factory().begin() as session:
            user = session.scalars(select(User).where(User.telegram_id == telegram_id)).one()
            vehicle = Vehicle(user_id=user.id, brand=brand, model=model,
                              year=year, license_plate=license_plate)
            session.add(vehicle)
        return vehicle
    except (SQLAlchemyError, ValueError, UserSyncError):
        raise VehicleError("Vehicle operation is temporarily unavailable.") from None
