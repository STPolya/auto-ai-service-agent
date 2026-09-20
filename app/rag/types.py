"""Knowledge values, independent of SQLAlchemy and Telegram."""

from dataclasses import dataclass

MAX_RAG_CHUNKS = 4
MAX_CHUNK_CHARS = 1800


@dataclass(frozen=True)
class KnowledgeItem:
    source: str
    title: str
    content: str
    category: str
