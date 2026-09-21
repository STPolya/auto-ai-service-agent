"""Reusable owned support requests. Provider I/O never holds a DB transaction."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.ai.handoff_summary import MAX_HANDOFF_MESSAGES, summarize_handoff
from app.ai.history import HistoryMessage
from app.database.models import Conversation, Message, SupportRequest, User, Vehicle
from app.database.session import get_session_factory
from app.services.support_status import ACTIVE_STATUSES, ALLOWED_STATUSES, NEW, can_transition

DEFAULT_ADMIN_PAGE_SIZE = 20
MAX_ADMIN_PAGE_SIZE = 100


class SupportRequestError(RuntimeError):
    """Safe persistence/ownership error for interface callers."""


class SupportRequestNotFound(SupportRequestError):
    """Requested support request does not exist."""


class InvalidStatusTransition(SupportRequestError):
    """The requested workflow transition is not allowed."""


@dataclass(frozen=True)
class SupportRequestResult:
    request: SupportRequest
    created: bool


def _owned_conversation(session, telegram_id: int, conversation_id: int, *, lock: bool = False):
    query = select(Conversation).join(User).where(
        Conversation.id == conversation_id, User.telegram_id == telegram_id,
    )
    if lock:
        query = query.with_for_update(of=Conversation)
    conversation = session.scalar(query)
    if conversation is None:
        raise SupportRequestError("Conversation unavailable.")
    return conversation


def _active(session, conversation_id: int):
    return session.scalar(select(SupportRequest).join(Conversation).where(
        SupportRequest.conversation_id == conversation_id,
        SupportRequest.user_id == Conversation.user_id,
        SupportRequest.status.in_(ACTIVE_STATUSES),
    ))


def _summary_history(session, conversation_id: int) -> list[HistoryMessage]:
    # Preserve the initial complaint (first 10) and recent clarification (last 20).
    # Two bounded queries; deduplicate overlap for short conversations.
    first = MAX_HANDOFF_MESSAGES // 3
    query = select(Message).where(Message.conversation_id == conversation_id)
    oldest = list(session.scalars(query.order_by(Message.id).limit(first)))
    newest = list(session.scalars(query.order_by(Message.id.desc()).limit(MAX_HANDOFF_MESSAGES - first)))
    rows = {row.id: row for row in oldest + newest}
    return [{"role": rows[key].role, "content": rows[key].content} for key in sorted(rows)]


def create_support_request(telegram_id: int, conversation_id: int) -> SupportRequestResult:
    try:
        factory = get_session_factory()
        with factory() as session:
            _owned_conversation(session, telegram_id, conversation_id)
            existing = _active(session, conversation_id)
            if existing is not None:
                return SupportRequestResult(existing, False)
            history = _summary_history(session, conversation_id)
        summary = summarize_handoff(history)
        try:
            with factory.begin() as session:
                conversation = _owned_conversation(session, telegram_id, conversation_id, lock=True)
                existing = _active(session, conversation_id)
                if existing is not None:
                    return SupportRequestResult(existing, False)
                request = SupportRequest(user_id=conversation.user_id, conversation_id=conversation.id,
                                         status=NEW, summary=summary)
                session.add(request)
                session.flush()
            return SupportRequestResult(request, True)
        except IntegrityError:
            # Protect against writers outside this service as well as racing clients.
            # The failed transaction has rolled back before we query again.
            with factory() as session:
                _owned_conversation(session, telegram_id, conversation_id)
                existing = _active(session, conversation_id)
                if existing is not None:
                    return SupportRequestResult(existing, False)
            raise
    except (SQLAlchemyError, ValueError):
        raise SupportRequestError("Support request temporarily unavailable.") from None


def get_active_support_request_for_conversation(telegram_id: int, conversation_id: int) -> SupportRequest | None:
    try:
        factory = get_session_factory()
        with factory() as session:
            _owned_conversation(session, telegram_id, conversation_id)
            return _active(session, conversation_id)
    except (SQLAlchemyError, ValueError):
        raise SupportRequestError("Support request temporarily unavailable.") from None


def get_support_request(telegram_id: int, request_id: int) -> SupportRequest | None:
    try:
        factory = get_session_factory()
        with factory() as session:
            return session.scalar(select(SupportRequest).join(User).join(
                Conversation, SupportRequest.conversation_id == Conversation.id,
            ).where(SupportRequest.id == request_id, User.telegram_id == telegram_id,
                    Conversation.user_id == User.id))
    except (SQLAlchemyError, ValueError):
        raise SupportRequestError("Support request temporarily unavailable.") from None


def list_user_support_requests(telegram_id: int, *, limit: int = 50) -> list[SupportRequest]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100.")
    try:
        factory = get_session_factory()
        with factory() as session:
            return list(session.scalars(select(SupportRequest).join(User).join(
                Conversation, SupportRequest.conversation_id == Conversation.id,
            ).where(User.telegram_id == telegram_id, Conversation.user_id == User.id)
                .order_by(SupportRequest.created_at.desc(), SupportRequest.id.desc()).limit(limit)))
    except (SQLAlchemyError, ValueError):
        raise SupportRequestError("Support request temporarily unavailable.") from None


def _admin_item(request: SupportRequest, user: User) -> dict:
    """Detached plain values; no lazy ORM objects escape the admin service."""
    return {
        "id": request.id, "status": request.status, "summary": request.summary,
        "created_at": request.created_at, "updated_at": request.updated_at,
        "conversation_id": request.conversation_id,
        "user": {"id": user.id, "telegram_id": user.telegram_id,
                 "username": user.username, "first_name": user.first_name},
    }


def list_support_requests(*, status: str | None = None, limit: int = DEFAULT_ADMIN_PAGE_SIZE, offset: int = 0) -> list[dict]:
    """Admin-only operation: callers must enforce authorization at their boundary."""
    if status is not None and status not in ALLOWED_STATUSES:
        raise ValueError("Unsupported support status.")
    if type(limit) is not int or not 1 <= limit <= MAX_ADMIN_PAGE_SIZE or type(offset) is not int or offset < 0:
        raise ValueError("Invalid pagination.")
    try:
        factory = get_session_factory()
        with factory() as session:
            query = select(SupportRequest, User).join(User, SupportRequest.user_id == User.id)
            if status is not None:
                query = query.where(SupportRequest.status == status)
            rows = session.execute(query.order_by(SupportRequest.created_at.desc(), SupportRequest.id.desc())
                                   .limit(limit).offset(offset))
            return [_admin_item(request, user) for request, user in rows]
    except (SQLAlchemyError, ValueError):
        raise SupportRequestError("Support request temporarily unavailable.") from None


def get_support_request_detail(request_id: int) -> dict:
    """Admin-only case view, including the actual retained conversation."""
    try:
        factory = get_session_factory()
        with factory() as session:
            row = session.execute(select(SupportRequest, User, Conversation)
                                  .join(User, SupportRequest.user_id == User.id)
                                  .join(Conversation, SupportRequest.conversation_id == Conversation.id)
                                  .where(SupportRequest.id == request_id)).one_or_none()
            if row is None:
                raise SupportRequestNotFound("Support request not found.")
            request, user, conversation = row
            result = _admin_item(request, user)
            result["conversation"] = {"id": conversation.id, "created_at": conversation.created_at}
            result["messages"] = [
                {"id": message.id, "role": message.role, "content": message.content, "created_at": message.created_at}
                for message in session.scalars(select(Message).where(Message.conversation_id == conversation.id)
                                               .order_by(Message.created_at, Message.id))
            ]
            result["vehicles"] = [
                {"id": vehicle.id, "brand": vehicle.brand, "model": vehicle.model,
                 "year": vehicle.year, "license_plate": vehicle.license_plate}
                for vehicle in session.scalars(select(Vehicle).where(Vehicle.user_id == user.id).order_by(Vehicle.id))
            ]
            return result
    except (SQLAlchemyError, ValueError):
        raise SupportRequestError("Support request temporarily unavailable.") from None


def update_support_request_status(request_id: int, status: str) -> dict:
    """Admin-only state change; lock before validating against the current state."""
    if status not in ALLOWED_STATUSES:
        raise ValueError("Unsupported support status.")
    try:
        with get_session_factory().begin() as session:
            request = session.scalar(select(SupportRequest).where(SupportRequest.id == request_id).with_for_update())
            if request is None:
                raise SupportRequestNotFound("Support request not found.")
            if not can_transition(request.status, status):
                raise InvalidStatusTransition("Status transition is not allowed.")
            if request.status != status:
                request.status = status
                session.flush()
                session.refresh(request)
            user = session.get(User, request.user_id)
            result = _admin_item(request, user)
        return result
    except (SQLAlchemyError, ValueError):
        raise SupportRequestError("Support request temporarily unavailable.") from None
