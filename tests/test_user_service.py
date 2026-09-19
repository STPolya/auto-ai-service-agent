"""Service tests use only an isolated in-memory database."""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.models import User
from app.services.user_service import UserSyncError, sync_user


class UserServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        factory_patch = patch("app.services.user_service.get_session_factory", return_value=self.factory)
        factory_patch.start()
        self.addCleanup(factory_patch.stop)

    def rows(self):
        with self.factory() as session:
            return list(session.scalars(select(User)))

    def test_new_user_is_persisted(self):
        user = sync_user(9876543210, "driver", "Alex")
        self.assertIsNotNone(user.id)
        self.assertIsNotNone(user.created_at)
        stored = self.rows()[0]
        self.assertEqual((stored.telegram_id, stored.username, stored.first_name),
                         (9876543210, "driver", "Alex"))

    def test_repeated_sync_preserves_identity_without_update(self):
        first = sync_user(123, "driver", "Alex")
        statements = []
        event.listen(self.engine, "before_cursor_execute",
                     lambda conn, cursor, statement, parameters, context, many: statements.append(statement))
        second = sync_user(123, "driver", "Alex")
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.created_at, second.created_at)
        self.assertEqual(len(self.rows()), 1)
        self.assertFalse(any(sql.lstrip().upper().startswith("UPDATE") for sql in statements))

    def test_profile_changes_are_persisted(self):
        first = sync_user(123, "old", "Old")
        second = sync_user(123, "new", "New")
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual((self.rows()[0].username, self.rows()[0].first_name), ("new", "New"))

    def test_nullable_profile_and_username_removal(self):
        user = sync_user(123, None, None)
        self.assertIsNone(user.username)
        self.assertIsNone(user.first_name)
        sync_user(123, "driver", "Alex")
        sync_user(123, None, None)
        self.assertIsNone(self.rows()[0].username)
        self.assertIsNone(self.rows()[0].first_name)

    def test_username_is_not_identity(self):
        sync_user(123, "driver", "Alex")
        sync_user(456, "driver", "Alex")
        self.assertEqual(len(self.rows()), 2)

    def test_commit_failure_rolls_back_and_sanitizes_error(self):
        def fail_commit(session):
            session.flush()
            raise OperationalError(None, None, RuntimeError("private-driver-detail"))

        event.listen(self.factory, "before_commit", fail_commit)
        with self.assertRaises(UserSyncError) as error:
            sync_user(123, "driver", "Alex")
        self.assertNotIn("private-driver-detail", str(error.exception))
        self.assertEqual(self.rows(), [])

    def test_configuration_failure_is_sanitized(self):
        with patch("app.services.user_service.get_session_factory", side_effect=ValueError("private-detail")):
            with self.assertRaises(UserSyncError) as error:
                sync_user(123, None, None)
        self.assertNotIn("private-detail", str(error.exception))

    def test_postgresql_conflict_target(self):
        statement = insert(User).values(telegram_id=123).on_conflict_do_nothing(
            index_elements=[User.telegram_id],
        )
        self.assertIn("ON CONFLICT (telegram_id) DO NOTHING", str(statement.compile(dialect=postgresql.dialect())))


if __name__ == "__main__":
    unittest.main()
