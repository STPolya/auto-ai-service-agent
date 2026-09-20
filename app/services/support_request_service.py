"""Reusable owned support requests. Provider I/O never holds a DB transaction."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.ai.handoff_summary import MAX_HANDOFF_MESSAGES, summarize_handoff
from app.ai.history import HistoryMessage
from app.database.models import Conversation, Message, SupportRequest, User
from app.database.session import get_session_factory
from app.services.support_status import ACTIVE_STATUSES, NEW


class SupportRequestError(RuntimeError):
    """Safe persistence/ownership error for interface callers."""


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
