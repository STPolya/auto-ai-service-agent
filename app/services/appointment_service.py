"""Appointment operations reusable by Telegram and a future API."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.database.models import Appointment, Service, User, Vehicle
from app.database.session import get_session_factory
from app.services.booking_rules import BookingValidationError, local_now, validate_appointment_at


class AppointmentError(RuntimeError):
    """Safe database failure for appointment callers."""


def _selection(session: Session, telegram_id: int, vehicle_id: int, service_id: int) -> tuple[Vehicle, Service]:
    vehicle = session.scalar(
        select(Vehicle).join(User).where(User.telegram_id == telegram_id, Vehicle.id == vehicle_id)
        .with_for_update(of=Vehicle)
    )
    if vehicle is None:
        raise BookingValidationError("That vehicle is no longer available. Please restart booking.")
    service = session.scalar(select(Service).where(Service.id == service_id, Service.is_active.is_(True)).with_for_update())
    if service is None:
        raise BookingValidationError("That service is no longer available. Please restart booking.")
    return vehicle, service


def get_booking_selection(telegram_id: int, vehicle_id: int, service_id: int) -> tuple[Vehicle, Service]:
    try:
        with get_session_factory().begin() as session:
            return _selection(session, telegram_id, vehicle_id, service_id)
    except BookingValidationError:
        raise
    except (SQLAlchemyError, ValueError):
        raise AppointmentError("Booking is temporarily unavailable.") from None


def create_appointment(telegram_id: int, vehicle_id: int, service_id: int, appointment_at: datetime) -> Appointment:
    # Validate at confirmation as well as during entry: time may have passed.
    appointment_at = validate_appointment_at(appointment_at)
    try:
        with get_session_factory().begin() as session:
            vehicle, service = _selection(session, telegram_id, vehicle_id, service_id)
            appointment = Appointment(
                user_id=vehicle.user_id, vehicle_id=vehicle.id, service_id=service.id,
                appointment_at=appointment_at.astimezone(timezone.utc), status="scheduled",
                problem_description=None,
            )
            session.add(appointment)
        return appointment
    except BookingValidationError:
        raise
    except (SQLAlchemyError, ValueError):
        raise AppointmentError("Booking is temporarily unavailable.") from None


def list_upcoming_appointments(telegram_id: int) -> list[Appointment]:
    try:
        with get_session_factory()() as session:
            return list(session.scalars(
                select(Appointment).join(User).where(
                    User.telegram_id == telegram_id,
                    Appointment.appointment_at >= local_now().astimezone(timezone.utc),
                ).options(joinedload(Appointment.vehicle), joinedload(Appointment.service))
                .order_by(Appointment.appointment_at, Appointment.id)
            ))
    except (SQLAlchemyError, ValueError):
        raise AppointmentError("Appointments are temporarily unavailable.") from None
