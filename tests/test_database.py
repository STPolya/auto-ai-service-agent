"""Offline checks: never read .env and never connect to PostgreSQL."""

import io
import os
import unittest
from unittest.mock import patch

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select
from sqlalchemy.orm import configure_mappers, sessionmaker

from app.config.settings import get_database_url
from app.database.base import Base
from app.database import models
from app.database.session import database_url, get_engine, get_session_factory


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"DATABASE_URL": ""})
        self.dotenv = patch("app.config.settings.load_dotenv")
        self.env.start()
        self.dotenv.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.dotenv.stop)
        self.config = Config("alembic.ini")
        self.scripts = ScriptDirectory.from_config(self.config)
        self.migrations = [revision.module for revision in reversed(list(self.scripts.walk_revisions()))]

    def test_configuration_and_lazy_engine(self):
        with self.assertRaisesRegex(ValueError, "DATABASE_URL is required"):
            get_database_url()
        for invalid in ("invalid-sensitive-value", "sqlite://", "postgresql://host:bad/db"):
            with patch.dict(os.environ, {"DATABASE_URL": invalid}):
                with self.assertRaises(ValueError) as error:
                    database_url()
                self.assertNotIn(invalid, str(error.exception))
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql:///offline_test"}):
            self.assertEqual(database_url().drivername, "postgresql+psycopg")
            with patch("psycopg.connect", side_effect=AssertionError("Network forbidden")):
                engine = get_engine()
                factory = get_session_factory()
                with factory() as session:
                    self.assertIs(session.bind, engine)
                engine.dispose()
                get_session_factory.cache_clear()
                get_engine.cache_clear()

    def test_models_and_migration_match(self):
        configure_mappers()
        self.assertEqual(set(Base.metadata.tables), {"users", "vehicles", "services", "appointments", "conversations", "messages", "knowledge_chunks", "support_requests"})
        self.assertTrue(models.User.__table__.c.created_at.type.timezone)
        self.assertTrue(models.Appointment.__table__.c.appointment_at.type.timezone)
        self.assertTrue(models.Conversation.__table__.c.created_at.type.timezone)
        self.assertTrue(models.Message.__table__.c.created_at.type.timezone)
        # SQLite is only an in-memory structural comparison, not a PostgreSQL substitute.
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
                with Operations.context(context):
                    for migration in self.migrations:
                        migration.upgrade()
                    self.assertEqual(compare_metadata(context, Base.metadata), [])
                    for migration in reversed(self.migrations):
                        migration.downgrade()
        finally:
            engine.dispose()

    def test_postgresql_sql_compilation(self):
        output = io.StringIO()
        context = MigrationContext.configure(dialect_name="postgresql", opts={
            "as_sql": True, "output_buffer": output, "target_metadata": Base.metadata,
        })
        with Operations.context(context):
            for migration in self.migrations:
                migration.upgrade()
            for migration in reversed(self.migrations):
                migration.downgrade()
        sql = output.getvalue()
        for table in Base.metadata.tables:
            self.assertIn(f"CREATE TABLE {table}", sql)
            self.assertIn(f"DROP TABLE {table}", sql)
        self.assertIn("TIMESTAMP WITH TIME ZONE", sql)
        self.assertIn("NUMERIC(12, 2)", sql)
        self.assertIn("CREATE UNIQUE INDEX ix_users_telegram_id", sql)
        self.assertIn("CREATE INDEX ix_knowledge_chunks_source", sql)
        self.assertIn("CREATE INDEX ix_knowledge_chunks_search", sql)
        self.assertIn("USING gin (to_tsvector('russian'::regconfig, title || ' ' || content))", sql)
        self.assertIn("WHERE is_active IS true", sql)
        self.assertIn("CREATE UNIQUE INDEX uq_support_requests_active_conversation", sql)
        self.assertIn("WHERE status IN ('new', 'in_progress')", sql)
        self.assertIn("ck_support_requests_status", sql)

    def test_session_commit_and_rollback(self):
        engine = create_engine("sqlite://")
        try:
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory.begin() as session:
                session.add(models.User(telegram_id=9876543210))
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with factory.begin() as session:
                    session.add(models.User(telegram_id=9876543211))
                    session.flush()
                    raise RuntimeError("rollback")
            with factory() as session:
                self.assertEqual(list(session.scalars(select(models.User.telegram_id))), [9876543210])
        finally:
            engine.dispose()

    def test_alembic_environment_loads_metadata_offline(self):
        seen = []

        def inspect_metadata(revision, context):
            self.assertIs(context.opts["target_metadata"], Base.metadata)
            seen.append(True)
            return []  # Load the environment without executing any migration.

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql:///offline_test"}):
            with patch("psycopg.connect", side_effect=AssertionError("Network forbidden")):
                with EnvironmentContext(
                    self.config, self.scripts, as_sql=True, fn=inspect_metadata,
                    output_buffer=io.StringIO(),
                ):
                    self.scripts.run_env()
        self.assertEqual(seen, [True])


if __name__ == "__main__":
    unittest.main()
