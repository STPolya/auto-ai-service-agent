"""Owned support requests and summaries without external provider/database access."""

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app.ai.client import GeminiError
from app.ai.handoff_prompts import SUMMARY_PROMPT, SUMMARY_SCHEMA
from app.ai.handoff_summary import MAX_HANDOFF_MESSAGES, fallback_summary, summarize_handoff
from app.database.base import Base
from app.database.models import Conversation, Message, SupportRequest, User
from app.services import support_request_service as service
from app.services.support_status import ACTIVE_STATUSES, ALLOWED_STATUSES, NEW, IN_PROGRESS, RESOLVED, CANCELLED


class SupportRequestTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory.begin() as session:
            user = User(telegram_id=100)
            other = User(telegram_id=200)
            session.add_all([user, other])
            session.flush()
            self.user_id = user.id
            first = Conversation(user_id=user.id)
            second = Conversation(user_id=other.id)
            session.add_all([first, second])
            session.flush()
            self.conversation_id, self.other_id = first.id, second.id
            session.add(Message(conversation_id=first.id, role="user", content="Toyota: вибрация при торможении"))
        for target in ("app.services.support_request_service.get_session_factory",
                       "app.services.conversation_service.get_session_factory"):
            patcher = patch(target, return_value=self.factory)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("app.services.support_request_service.summarize_handoff", return_value="Жалоба: вибрация при торможении.")
        self.summary = patcher.start()
        self.addCleanup(patcher.stop)

    def test_schema_defaults_owner_summary_and_read_operations(self):
        columns = SupportRequest.__table__.c
        self.assertEqual(set(columns.keys()), {"id", "user_id", "conversation_id", "status", "summary", "created_at", "updated_at"})
        self.assertTrue(columns.summary.nullable)
        self.assertTrue(columns.created_at.type.timezone)
        self.assertTrue(columns.updated_at.type.timezone)
        self.assertEqual(set(ALLOWED_STATUSES), {"new", "in_progress", "resolved", "cancelled"})
        result = service.create_support_request(100, self.conversation_id)
        self.assertTrue(result.created)
        request = result.request
        self.assertEqual((request.user_id, request.conversation_id, request.status), (self.user_id, self.conversation_id, NEW))
        self.assertEqual(request.summary, self.summary.return_value)
        self.assertIsNotNone(request.created_at)
        self.assertIsNotNone(request.updated_at)
        self.assertEqual(service.get_support_request(100, request.id).id, request.id)
        self.assertIsNone(service.get_support_request(200, request.id))
        self.assertEqual([r.id for r in service.list_user_support_requests(100)], [request.id])
        self.assertEqual(service.list_user_support_requests(200), [])

    def test_forged_missing_or_other_users_conversation_is_rejected(self):
        for telegram_id, conversation_id in ((200, self.conversation_id), (100, self.other_id), (100, 9999), (9999, self.conversation_id)):
            with self.assertRaises(service.SupportRequestError):
                service.create_support_request(telegram_id, conversation_id)
            with self.assertRaises(service.SupportRequestError):
                service.get_active_support_request_for_conversation(telegram_id, conversation_id)
        self.summary.assert_not_called()

    def test_duplicate_active_returns_existing_without_new_summary(self):
        first = service.create_support_request(100, self.conversation_id)
        for status in ACTIVE_STATUSES:
            with self.factory.begin() as session:
                session.get(SupportRequest, first.request.id).status = status
            duplicate = service.create_support_request(100, self.conversation_id)
            self.assertFalse(duplicate.created)
            self.assertEqual(duplicate.request.id, first.request.id)
        self.summary.assert_called_once()
        self.assertEqual(service.get_active_support_request_for_conversation(100, self.conversation_id).id, first.request.id)

    def test_resolved_cancelled_history_allows_new_request(self):
        for status in (RESOLVED, CANCELLED):
            request = service.create_support_request(100, self.conversation_id).request
            with self.factory.begin() as session:
                session.get(SupportRequest, request.id).status = status
            self.assertIsNone(service.get_active_support_request_for_conversation(100, self.conversation_id))
        self.assertTrue(service.create_support_request(100, self.conversation_id).created)
        self.assertEqual(len(service.list_user_support_requests(100)), 3)

    def test_database_unique_active_check_and_foreign_key_constraints(self):
        service.create_support_request(100, self.conversation_id)
        for status in ACTIVE_STATUSES:
            with self.assertRaises(IntegrityError):
                with self.factory.begin() as session:
                    session.add(SupportRequest(user_id=self.user_id, conversation_id=self.conversation_id, status=status))
        with self.assertRaises(IntegrityError):
            with self.factory.begin() as session:
                session.add(SupportRequest(user_id=self.user_id, conversation_id=self.other_id, status="invalid"))
        with self.assertRaises(IntegrityError):
            with self.factory.begin() as session:
                session.add(SupportRequest(user_id=9999, conversation_id=9999))

    def test_bounded_summary_input_preserves_first_complaint_and_recent_turns(self):
        with self.factory.begin() as session:
            session.add_all(Message(conversation_id=self.conversation_id, role="user" if i % 2 else "assistant", content=str(i))
                            for i in range(1, 45))
            session.add(Message(conversation_id=self.other_id, role="user", content="private other user"))
        service.create_support_request(100, self.conversation_id)
        history = self.summary.call_args.args[0]
        self.assertEqual(MAX_HANDOFF_MESSAGES, 30)
        self.assertEqual(len(history), 30)
        self.assertIn("Toyota", history[0]["content"])
        self.assertEqual([e["content"] for e in history[1:]], [str(i) for i in list(range(1, 10)) + list(range(25, 45))])

    def test_provider_failure_creates_request_with_local_summary(self):
        self.summary.side_effect = summarize_handoff
        with patch("app.ai.handoff_summary.generate_response", side_effect=GeminiError("private key")):
            with self.assertLogs("app.ai.handoff_summary", level="WARNING") as logs:
                result = service.create_support_request(100, self.conversation_id)
        self.assertTrue(result.created)
        self.assertIn("Toyota", result.request.summary)
        self.assertNotIn("private key", result.request.summary)
        self.assertNotIn("Toyota", " ".join(logs.output))
        self.assertNotIn("private key", " ".join(logs.output))

    def test_empty_conversation_still_creates_request(self):
        self.summary.side_effect = summarize_handoff
        with patch("app.ai.handoff_summary.generate_response") as provider:
            result = service.create_support_request(200, self.other_id)
        provider.assert_not_called()
        self.assertTrue(result.created)
        self.assertIn("пока не содержит", result.request.summary)

    def test_handoff_preserves_messages_and_diagnostics_remain_usable(self):
        from app.services.conversation_service import diagnostic_turn
        with self.factory() as session:
            before = [(m.id, m.content) for m in session.scalars(select(Message).order_by(Message.id))]
        service.create_support_request(100, self.conversation_id)
        with self.factory() as session:
            self.assertEqual([(m.id, m.content) for m in session.scalars(select(Message).order_by(Message.id))], before)
        with patch("app.services.conversation_service.retrieve_context", return_value=[]) as rag, \
             patch("app.services.conversation_service.diagnose_problem", return_value="Ответ"):
            self.assertEqual(diagnostic_turn(100, self.conversation_id, "На скорости"), "Ответ")
        rag.assert_called_once_with("На скорости")
        with self.factory() as session:
            self.assertEqual(len(list(session.scalars(select(Message)))), len(before) + 2)

    def test_updated_at_changes_on_orm_status_update(self):
        request = service.create_support_request(100, self.conversation_id).request
        with self.factory.begin() as session:
            session.get(SupportRequest, request.id).updated_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        with self.factory.begin() as session:
            session.get(SupportRequest, request.id).status = IN_PROGRESS
        self.assertGreater(service.get_support_request(100, request.id).updated_at.year, 2000)

    def test_database_failure_is_sanitized(self):
        with patch.object(service, "get_session_factory", side_effect=OperationalError("private SQL", {}, Exception("private URL"))):
            with self.assertRaises(service.SupportRequestError) as error:
                service.create_support_request(100, self.conversation_id)
        self.assertNotIn("private", str(error.exception))


class SummaryTests(unittest.TestCase):
    def test_single_user_message_appears_once_without_clarification_section(self):
        complaint = "Вибрация руля при торможении."
        result = fallback_summary([{"role": "user", "content": complaint}])
        self.assertEqual(result.count(complaint), 1)
        self.assertIn("Первое сообщение клиента: " + complaint, result)
        self.assertNotIn("Последние уточнения клиента:", result)

    def test_later_user_clarifications_are_chronological_and_exclude_first(self):
        messages = ["Вибрация руля.", "Модель Toyota.", "После 80 км/ч.", "Только при торможении.", "Началось вчера."]
        result = fallback_summary([{"role": "user", "content": content} for content in messages])
        self.assertEqual(result.count(messages[0]), 1)
        clarification_lines = result.split("Последние уточнения клиента:\n", 1)[1].splitlines()
        self.assertEqual(clarification_lines[:3], ["- " + content for content in messages[-3:]])
        self.assertNotIn(messages[0], "\n".join(clarification_lines))

    def test_assistant_messages_are_not_customer_clarifications(self):
        complaint = {"role": "user", "content": "Вибрация руля."}
        assistant = {"role": "assistant", "content": "На какой скорости?"}
        result = fallback_summary([complaint, assistant])
        self.assertNotIn("Последние уточнения клиента:", result)
        result = fallback_summary([complaint, assistant, {"role": "user", "content": "После 80 км/ч."}])
        clarifications = result.split("Последние уточнения клиента:\n", 1)[1].split("Последний ответ AI", 1)[0]
        self.assertEqual(clarifications, "- После 80 км/ч.\n")
        self.assertNotIn(assistant["content"], clarifications)

    def test_separate_prompt_schema_and_chronological_input(self):
        history = [{"role": "user", "content": "Toyota"}, {"role": "assistant", "content": "Когда?"},
                   {"role": "user", "content": "При торможении"}]
        with patch("app.ai.handoff_summary.generate_response", return_value=json.dumps({"summary": "Жалоба: вибрация."})) as provider:
            self.assertEqual(summarize_handoff(history), "Жалоба: вибрация.")
        args = provider.call_args
        self.assertEqual(args.args[0], SUMMARY_PROMPT)
        self.assertEqual(json.loads(args.args[1]), history)
        self.assertEqual(args.kwargs["response_schema"], SUMMARY_SCHEMA)

    def test_invalid_provider_output_uses_deterministic_useful_fallback(self):
        history = [{"role": "user", "content": "Toyota Corolla 2018. Вибрация руля."},
                   {"role": "assistant", "content": "Нужен осмотр тормозов."}]
        expected = fallback_summary(history)
        self.assertEqual(expected, fallback_summary(history))
        for raw in ("broken", "{}", "[]", '{"summary":""}', json.dumps({"summary": "x" * 3001})):
            with patch("app.ai.handoff_summary.generate_response", return_value=raw):
                self.assertEqual(summarize_handoff(history), expected)
        self.assertIn("Toyota Corolla", expected)
        self.assertIn("осмотр тормозов", expected)

    def test_summary_input_and_fallback_are_bounded(self):
        history = [{"role": "user", "content": str(i) + "x" * 5000} for i in range(50)]
        with patch("app.ai.handoff_summary.generate_response", return_value='{"summary":"Кратко"}') as provider:
            summarize_handoff(history)
        sent = json.loads(provider.call_args.args[1])
        self.assertEqual(len(sent), MAX_HANDOFF_MESSAGES)
        self.assertTrue(all(len(entry["content"]) <= 3000 for entry in sent))
        self.assertLessEqual(len(fallback_summary(history)), 3000)
