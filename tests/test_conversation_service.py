"""Conversation persistence tests use only an isolated in-memory SQLite database."""

import ast
import inspect
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.ai.service import DiagnosticError, DiagnosticInputError
from app.bot.handlers import diagnostics
from app.database.base import Base
from app.database.models import Conversation, Message, User
from app.services import conversation_service as service


class ConversationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)
        for target in ("app.services.conversation_service.get_session_factory",
                       "app.services.user_service.get_session_factory"):
            patcher = patch(target, return_value=self.factory)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("app.services.conversation_service.diagnose_problem", return_value="Ответ на вопрос")
        self.ai = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("app.services.conversation_service.retrieve_context", return_value=[])
        self.retrieve = patcher.start()
        self.addCleanup(patcher.stop)
        self.conversation = service.create_conversation(100, None, "Alex")

    def messages(self):
        with self.factory() as session:
            return list(session.scalars(select(Message).order_by(Message.id)))

    def test_creation_reuses_persisted_user_and_always_creates_new_conversation(self):
        second = service.create_conversation(100, "alex", "Alex")
        self.assertNotEqual(second, self.conversation)
        with self.factory() as session:
            users = list(session.scalars(select(User)))
            self.assertEqual(len(users), 1)
            conversations = list(session.scalars(select(Conversation)))
            self.assertEqual([c.user_id for c in conversations], [users[0].id] * 2)
            self.assertTrue(all(c.created_at for c in conversations))

    def test_success_persists_both_messages_and_current_message_only_once(self):
        self.assertEqual(service.diagnostic_turn(100, self.conversation, "  Вибрация  "), "Ответ на вопрос")
        self.assertEqual([(m.role, m.content) for m in self.messages()],
                         [("user", "Вибрация"), ("assistant", "Ответ на вопрос")])
        self.ai.assert_called_once_with([{"role": "user", "content": "Вибрация"}], knowledge=[])

    def test_second_turn_uses_previous_context(self):
        service.diagnostic_turn(100, self.conversation, "Вибрация при торможении")
        service.diagnostic_turn(100, self.conversation, "После 80 км/ч")
        self.ai.assert_called_with([
            {"role": "user", "content": "Вибрация при торможении"},
            {"role": "assistant", "content": "Ответ на вопрос"},
            {"role": "user", "content": "После 80 км/ч"},
        ], knowledge=[])

    def test_failed_provider_persists_only_user_and_conversation_remains_usable(self):
        self.ai.side_effect = DiagnosticError("provider failed")
        with self.assertRaises(DiagnosticError):
            service.diagnostic_turn(100, self.conversation, "Вибрация")
        self.assertEqual([(m.role, m.content) for m in self.messages()], [("user", "Вибрация")])
        self.ai.side_effect = None
        service.diagnostic_turn(100, self.conversation, "При торможении")
        self.assertEqual(len(self.messages()), 3)
        self.ai.assert_called_with([{"role": "user", "content": "Вибрация"},
                                    {"role": "user", "content": "При торможении"}], knowledge=[])

    def test_rag_uses_only_current_query_and_does_not_persist_chunks(self):
        from app.rag.types import KnowledgeItem
        knowledge = [KnowledgeItem("brakes.md", "Тормоза", "Справочные сведения", "brakes")]
        self.retrieve.return_value = knowledge
        service.diagnostic_turn(100, self.conversation, "Вибрация")
        service.diagnostic_turn(100, self.conversation, "При торможении")
        self.assertEqual([call.args for call in self.retrieve.call_args_list], [("Вибрация",), ("При торможении",)])
        self.ai.assert_called_with([
            {"role": "user", "content": "Вибрация"},
            {"role": "assistant", "content": "Ответ на вопрос"},
            {"role": "user", "content": "При торможении"},
        ], knowledge=knowledge)
        self.assertEqual(len(self.messages()), 4)
        self.assertNotIn("Справочные сведения", [row.content for row in self.messages()])

    def test_retrieval_failure_logs_safely_and_continues_without_rag(self):
        from app.rag.retriever import RetrievalError
        self.retrieve.side_effect = RetrievalError("private SQL or credentials")
        with self.assertLogs("app.services.conversation_service", level="WARNING") as logs:
            self.assertEqual(service.diagnostic_turn(100, self.conversation, "private symptom"), "Ответ на вопрос")
        self.assertNotIn("private", " ".join(logs.output))
        self.ai.assert_called_once_with([{"role": "user", "content": "private symptom"}], knowledge=[])
        self.assertEqual(len(self.messages()), 2)

    def test_retrieval_programming_errors_are_not_swallowed(self):
        self.retrieve.side_effect = TypeError("programming error")
        with self.assertRaises(TypeError):
            service.diagnostic_turn(100, self.conversation, "Вибрация")
        self.ai.assert_not_called()

    def test_context_limit_order_and_conversation_isolation(self):
        second = service.create_conversation(100, None, "Alex")
        other = service.create_conversation(200, None, "Other")
        service.append_message(100, second, "user", "another conversation")
        service.append_message(200, other, "user", "another user")
        for index in range(15):
            service.append_message(100, self.conversation, "user", str(index))
        history = service.recent_messages(100, self.conversation)
        self.assertEqual(service.MAX_CONTEXT_MESSAGES, 10)
        self.assertEqual([entry["content"] for entry in history], [str(i) for i in range(5, 15)])

    def test_ownership_checked_for_read_write_and_turn(self):
        for conversation in (self.conversation, 99999):
            with self.assertRaises(service.ConversationError):
                service.recent_messages(200, conversation)
            with self.assertRaises(service.ConversationError):
                service.append_message(200, conversation, "assistant", "forged")
            with self.assertRaises(service.ConversationError):
                service.diagnostic_turn(200, conversation, "forged")
        self.assertEqual(self.messages(), [])
        self.ai.assert_not_called()

    def test_invalid_input_or_role_creates_no_message(self):
        for text in ("", " ", "x" * 3001):
            with self.assertRaises(DiagnosticInputError):
                service.diagnostic_turn(100, self.conversation, text)
        with self.assertRaises(service.ConversationError):
            service.append_message(100, self.conversation, "system", "override")
        self.assertEqual(self.messages(), [])
        self.ai.assert_not_called()

    def test_database_errors_are_sanitized(self):
        from sqlalchemy.exc import OperationalError
        with patch.object(service, "get_session_factory", side_effect=OperationalError("private", {}, Exception("private"))):
            with self.assertRaises(service.ConversationError) as error:
                service.recent_messages(100, self.conversation)
        self.assertNotIn("private", str(error.exception))

    def test_telegram_handler_has_no_database_imports(self):
        tree = ast.parse(inspect.getsource(diagnostics))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name.startswith(("sqlalchemy", "app.database")) for name in imports))
