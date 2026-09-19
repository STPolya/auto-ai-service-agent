"""Synchronize Telegram profiles using a short, synchronous transaction."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.database.models import User
from app.database.session import get_session_factory


class UserSyncError(RuntimeError):
    """A user could not be persisted; safe to handle at the Telegram boundary."""


def sync_user(
    telegram_id: int, username: str | None, first_name: str | None,
) -> User:
    try:
        factory = get_session_factory()
        with factory.begin() as session:
            # The unique index arbitrates concurrent first-time registrations.
            session.execute(
                insert(User).values(
                    telegram_id=telegram_id, username=username, first_name=first_name,
                ).on_conflict_do_nothing(index_elements=[User.telegram_id])
            )
            user = session.scalars(
                select(User).where(User.telegram_id == telegram_id).with_for_update()
            ).one()
            if user.username != username:
                user.username = username
            if user.first_name != first_name:
                user.first_name = first_name
        # The existing factory uses expire_on_commit=False, so scalar fields
        # remain readable after the context commits and closes the session.
        return user
    except (SQLAlchemyError, ValueError):
        # Neither driver errors nor configuration values cross this boundary.
        raise UserSyncError("User synchronization is temporarily unavailable.") from None
