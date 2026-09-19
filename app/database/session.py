"""Lazy engine and session factory. No database work happens during import."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_database_url


def database_url() -> URL:
    """Normalize PostgreSQL URLs to psycopg 3 without exposing invalid input."""
    raw_url = get_database_url()
    try:
        url = make_url(raw_url)
        if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
            raise ValueError
        return url.set(drivername="postgresql+psycopg")
    except (ArgumentError, ValueError, TypeError):
        raise ValueError("DATABASE_URL must be a valid PostgreSQL connection URL.") from None


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True, echo=False, hide_parameters=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Use factory.begin() for commit/rollback and automatic session cleanup."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)
