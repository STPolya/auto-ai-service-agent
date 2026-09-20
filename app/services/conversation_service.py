"""Persistent diagnostic turns. No transaction is held during provider I/O."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.ai.history import HistoryMessage
from app.ai.service import diagnose_problem, validate_problem
from app.database.models import Conversation, Message, User
from app.database.session import get_session_factory
from app.services.user_service import UserSyncError, sync_user
from app.rag.retriever import RetrievalError, retrieve_context

MAX_CONTEXT_MESSAGES = 10
logger = logging.getLogger(__name__)


class ConversationError(RuntimeError):
    """Sanitized persistence or ownership failure."""


def create_conversation(telegram_id: int, username: str | None, first_name: str | None) -> int:
    try:
        user = sync_user(telegram_id, username, first_name)
        with get_session_factory().begin() as session:
            conversation = Conversation(user_id=user.id)
            session.add(conversation)
            session.flush()
            conversation_id = conversation.id
        return conversation_id
    except (SQLAlchemyError, ValueError, UserSyncError):
        raise ConversationError("Conversation is temporarily unavailable.") from None


def _check_owner(session, telegram_id: int, conversation_id: int) -> None:
    owned = session.scalar(
        select(Conversation.id).join(User).where(
            Conversation.id == conversation_id, User.telegram_id == telegram_id,
        )
    )
    if owned is None:
        raise ConversationError("Conversation is unavailable.")


def append_message(telegram_id: int, conversation_id: int, role: str, content: str) -> None:
    if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
        raise ConversationError("Invalid conversation message.")
    try:
        with get_session_factory().begin() as session:
            _check_owner(session, telegram_id, conversation_id)
            session.add(Message(conversation_id=conversation_id, role=role, content=content))
    except (SQLAlchemyError, ValueError):
        raise ConversationError("Conversation is temporarily unavailable.") from None


def recent_messages(telegram_id: int, conversation_id: int) -> list[HistoryMessage]:
    try:
        factory = get_session_factory()
        with factory() as session:
            _check_owner(session, telegram_id, conversation_id)
            rows = list(session.scalars(
                select(Message).where(Message.conversation_id == conversation_id)
                .order_by(Message.id.desc()).limit(MAX_CONTEXT_MESSAGES)
            ))
            return [{"role": row.role, "content": row.content} for row in reversed(rows)]
    except (SQLAlchemyError, ValueError):
        raise ConversationError("Conversation is temporarily unavailable.") from None


def diagnostic_turn(telegram_id: int, conversation_id: int, problem: str) -> str:
    problem = validate_problem(problem)
    append_message(telegram_id, conversation_id, "user", problem)
    # The current message is already in persisted history; never append it again.
    history = recent_messages(telegram_id, conversation_id)
    try:
        knowledge = retrieve_context(problem)
    except RetrievalError:
        logger.warning("Knowledge retrieval failed: category=database_or_search; continuing without knowledge.")
        knowledge = []
    answer = diagnose_problem(history, knowledge=knowledge)
    append_message(telegram_id, conversation_id, "assistant", answer)
    return answer
