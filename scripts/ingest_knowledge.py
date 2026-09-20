"""Explicit manual ingestion: python -m scripts.ingest_knowledge."""

from app.rag.ingestion import IngestionError, ingest_knowledge


def main() -> None:
    try:
        count = ingest_knowledge()
    except IngestionError:
        raise SystemExit("Knowledge ingestion failed; check documents and database configuration.") from None
    print(f"Knowledge ingestion complete: {count} chunks in supplied documents.")


if __name__ == "__main__":
    main()
