"""Read the active service catalog without Telegram dependencies."""

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database.models import Service
from app.database.session import get_session_factory


class CatalogError(RuntimeError):
    """Safe error for catalog consumers."""


def get_active_services() -> list[Service]:
    try:
        with get_session_factory()() as session:
            return list(session.scalars(
                select(Service).where(Service.is_active.is_(True)).order_by(Service.id)
            ))
    except (SQLAlchemyError, ValueError):
        raise CatalogError("Service catalog is temporarily unavailable.") from None
