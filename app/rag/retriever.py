"""Bounded Russian PostgreSQL lexical retrieval; no provider or Telegram coupling."""

import re

from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import websearch_to_tsquery
from sqlalchemy.exc import SQLAlchemyError

from app.database.models import KnowledgeChunk, knowledge_search_vector
from app.database.session import get_session_factory
from app.rag.types import KnowledgeItem, MAX_RAG_CHUNKS


class RetrievalError(RuntimeError):
    """Sanitized database/search failure."""


def search_statement(query: str, limit: int = MAX_RAG_CHUNKS):
    if type(limit) is not int or limit < 1:
        raise ValueError("Retrieval limit must be a positive integer.")
    # OR improves recall for conversational input. PostgreSQL handles Russian
    # stemming and stop words. User punctuation never becomes query operators.
    words = re.findall(r"[^\W_]+", query[:3000].lower(), flags=re.UNICODE)[:64]
    if not words:
        return None
    search = websearch_to_tsquery(literal_column("'russian'::regconfig"), " OR ".join(words))
    vector = knowledge_search_vector()
    return (
        select(KnowledgeChunk)
        .where(KnowledgeChunk.is_active.is_(True), vector.op("@@")(search))
        .order_by(func.ts_rank_cd(vector, search, 32).desc(), KnowledgeChunk.id.asc())
        .limit(min(limit, MAX_RAG_CHUNKS))
    )


def retrieve_context(query: str, limit: int = MAX_RAG_CHUNKS) -> list[KnowledgeItem]:
    statement = search_statement(query, limit)
    if statement is None:
        return []
    try:
        factory = get_session_factory()
    except ValueError:
        raise RetrievalError("Knowledge retrieval unavailable.") from None
    try:
        with factory() as session:
            return [KnowledgeItem(row.source, row.title, row.content, row.category)
                    for row in session.scalars(statement)]
    except SQLAlchemyError:
        raise RetrievalError("Knowledge retrieval unavailable.") from None
