"""Deterministic Markdown sections and atomic source replacement."""

from pathlib import Path
import re

from sqlalchemy import delete, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.database.models import KnowledgeChunk
from app.database.session import get_session_factory
from app.rag.types import KnowledgeItem, MAX_CHUNK_CHARS

KNOWLEDGE_DIRECTORY = Path(__file__).resolve().parents[2] / "knowledge_base"


class IngestionError(RuntimeError):
    """Safe error for the manual ingestion command."""


def chunk_markdown(source: str, markdown: str) -> list[KnowledgeItem]:
    category = Path(source).stem
    document_title = category
    section = ""
    lines = []
    chunks = []

    def flush():
        content = " ".join(" ".join(lines).split())
        title = document_title + (" — " + section if section else "")
        if len(source) > 255 or len(title) > 255 or len(category) > 100:
            raise IngestionError("Knowledge metadata exceeds allowed length.")
        while content:
            end = min(len(content), MAX_CHUNK_CHARS)
            if end < len(content):
                boundary = content.rfind(" ", 0, end + 1)
                if boundary > 0:
                    end = boundary
            chunks.append(KnowledgeItem(source, title, content[:end].strip(), category))
            content = content[end:].strip()
        lines.clear()

    for line in markdown.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            flush()
            if len(heading[1]) == 1:
                document_title, section = heading[2], ""
            else:
                section = heading[2]
        else:
            lines.append(line)
    flush()
    return chunks


def load_documents(directory: Path = KNOWLEDGE_DIRECTORY) -> dict[str, list[KnowledgeItem]]:
    paths = sorted(directory.glob("*.md"))
    if not paths:
        raise IngestionError("No knowledge documents found.")
    # A repository symlink named *.md must not copy a local secret into the KB.
    root = directory.resolve()
    if any(path.is_symlink() or not path.is_file() or path.resolve().parent != root for path in paths):
        raise IngestionError("Knowledge sources must be regular files within the document directory.")
    return {path.name: chunk_markdown(path.name, path.read_text(encoding="utf-8-sig")) for path in paths}


def ingest_knowledge(directory: Path = KNOWLEDGE_DIRECTORY) -> int:
    """Replace changed provided sources. Preserve unchanged IDs and active flags.

    Sources absent from the input directory are deliberately left untouched.
    An explicitly emptied source clears its chunks. Changed sources become active.
    """
    try:
        documents = load_documents(directory)
        with get_session_factory().begin() as session:
            # Serialize manual ingestions, including concurrent first ingestion.
            if session.bind.dialect.name == "postgresql":
                session.execute(text("LOCK TABLE knowledge_chunks IN SHARE ROW EXCLUSIVE MODE"))
            for source, chunks in documents.items():
                existing = list(session.scalars(select(KnowledgeChunk).where(
                    KnowledgeChunk.source == source,
                ).order_by(KnowledgeChunk.id)))
                if [KnowledgeItem(row.source, row.title, row.content, row.category) for row in existing] == chunks:
                    continue
                session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source == source))
                session.add_all(KnowledgeChunk(source=item.source, title=item.title,
                                              content=item.content, category=item.category, is_active=True)
                                for item in chunks)
        return sum(len(chunks) for chunks in documents.values())
    except (SQLAlchemyError, ValueError, OSError):
        raise IngestionError("Knowledge ingestion failed; check documents and database configuration.") from None
