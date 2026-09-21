"""In-process HTTP tests with isolated SQLite; never call external services."""

import ast
import asyncio
from datetime import datetime, timezone
import importlib
import inspect
import json
import os
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.app import app
from app.api import routes
from app.database.base import Base
from app.database.models import Conversation, Message, SupportRequest, User, Vehicle
from app.services import support_request_service as service
from app.services.support_status import ALLOWED_STATUSES, CANCELLED, IN_PROGRESS, NEW, RESOLVED, can_transition

FAKE_KEY = "offline-admin-key-not-a-real-secret"
PREFIX = "/api/admin/support-requests"


class AdminAPITests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)
        for patcher in (
            patch("app.services.support_request_service.get_session_factory", return_value=self.factory),
            patch("app.config.settings.load_dotenv"),
            patch.dict(os.environ, {"ADMIN_API_KEY": FAKE_KEY}),
            patch("app.ai.client.genai.Client", side_effect=AssertionError("Gemini is forbidden")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        with self.factory.begin() as session:
            session.add_all([User(id=1, telegram_id=100, first_name="Alex"),
                             User(id=2, telegram_id=200, username="other")])
            session.flush()
            session.add_all([Conversation(id=i, user_id=1 if i == 1 else 2) for i in range(1, 5)])
            session.flush()
            for index, (status, day) in enumerate(((NEW, 2), (IN_PROGRESS, 3), (RESOLVED, 3), (CANCELLED, 1)), 1):
                date = datetime(2026, 1, day, tzinfo=timezone.utc)
                session.add(SupportRequest(id=index, conversation_id=index, user_id=1 if index == 1 else 2,
                                           status=status, summary=None if index == 1 else "Резюме",
                                           created_at=date, updated_at=date))
            session.add_all([
                Message(id=1, conversation_id=1, role="assistant", content="Позднее", created_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
                Message(id=2, conversation_id=1, role="user", content="Ранее", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
                Message(id=3, conversation_id=2, role="user", content="Другой диалог"),
                Vehicle(id=1, user_id=1, brand="Toyota", model="Corolla", year=None, license_plate=None),
                Vehicle(id=2, user_id=2, brand="Other", model="Car"),
            ])
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Admin-Key": FAKE_KEY}

    def get(self, path=PREFIX, **kwargs):
        return self.client.get(path, headers=self.headers, **kwargs)

    def update(self, request_id, status):
        return self.client.patch(f"{PREFIX}/{request_id}/status", headers=self.headers, json={"status": status})

    def test_api_import_does_not_start_telegram_or_database(self):
        with patch("aiogram.Dispatcher.start_polling") as polling, \
             patch("app.database.session.get_engine") as engine, \
             patch("app.config.settings.load_dotenv") as dotenv:
            imported = importlib.reload(importlib.import_module("app.api.app"))
        self.assertIsNotNone(imported.app)
        polling.assert_not_called()
        engine.assert_not_called()
        dotenv.assert_not_called()

    def test_health_is_public_minimal_and_independent_of_configuration(self):
        with patch("app.api.dependencies.get_admin_api_key", side_effect=AssertionError("Must not read key")), \
             patch.object(service, "get_session_factory", side_effect=AssertionError("Must not connect")):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_all_admin_operations_require_header_key_before_service_calls(self):
        for header in ({}, {"X-Admin-Key": "wrong"}):
            with patch.object(service, "list_support_requests") as listing, \
                 patch.object(service, "get_support_request_detail") as detail, \
                 patch.object(service, "update_support_request_status") as update:
                self.assertEqual(self.client.get(PREFIX, headers=header).status_code, 401)
                self.assertEqual(self.client.get(PREFIX + "/1", headers=header).status_code, 401)
                self.assertEqual(self.client.patch(PREFIX + "/1/status", headers=header, json={"status": IN_PROGRESS}).status_code, 401)
            listing.assert_not_called()
            detail.assert_not_called()
            update.assert_not_called()

    def test_key_in_query_parameters_is_not_authentication(self):
        for parameter in ("X-Admin-Key", "ADMIN_API_KEY", "api_key"):
            response = self.client.get(PREFIX, params={parameter: FAKE_KEY})
            self.assertEqual(response.status_code, 401)
            self.assertNotIn(FAKE_KEY, response.text)

    def test_missing_or_placeholder_server_key_fails_closed(self):
        for configured in ("", "replace_with_a_strong_random_value"):
            with patch.dict(os.environ, {"ADMIN_API_KEY": configured}), \
                 self.assertLogs("app.api.dependencies", level="ERROR") as logs:
                response = self.get()
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(FAKE_KEY, response.text + " ".join(logs.output))

    def test_correct_key_lists_newest_first_with_tie_break_and_explicit_fields(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], [3, 2, 1, 4])
        item = response.json()[2]
        self.assertEqual(set(item), {"id", "status", "summary", "created_at", "updated_at", "conversation_id", "user"})
        self.assertEqual(item["user"], {"id": 1, "telegram_id": 100, "username": None, "first_name": "Alex"})
        self.assertIsNone(item["summary"])

    def test_status_filter(self):
        for status in ALLOWED_STATUSES:
            response = self.get(params={"status": status})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([item["status"] for item in response.json()], [status])

    def test_pagination_and_validation(self):
        self.assertEqual([item["id"] for item in self.get(params={"limit": 2, "offset": 1}).json()], [2, 1])
        self.assertEqual(self.get(params={"offset": 100}).json(), [])
        self.assertEqual(self.get(params={"limit": 100}).status_code, 200)
        for params in ({"limit": 101}, {"limit": 0}, {"limit": -1}, {"offset": -1},
                       {"offset": "invalid"}, {"limit": "1.5"}, {"status": "unsupported"}):
            self.assertEqual(self.get(params=params).status_code, 422)
        with patch.object(service, "list_support_requests", return_value=[]) as listing:
            self.get()
        listing.assert_called_once_with(status=None, limit=20, offset=0)

    def test_detail_contains_ordered_messages_and_only_owner_vehicles(self):
        response = self.get(PREFIX + "/1")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["conversation"]["id"], 1)
        self.assertEqual([message["content"] for message in detail["messages"]], ["Ранее", "Позднее"])
        self.assertEqual([vehicle["id"] for vehicle in detail["vehicles"]], [1])
        self.assertIsNone(detail["vehicles"][0]["year"])
        self.assertIsNone(detail["vehicles"][0]["license_plate"])
        self.assertEqual(set(detail["messages"][0]), {"id", "role", "content", "created_at"})
        for private in (FAKE_KEY, "DATABASE_URL", "GEMINI_API_KEY", "system_prompt", "knowledge_chunks"):
            self.assertNotIn(private, response.text)

    def test_missing_support_request_is_404_and_invalid_ids_are_422(self):
        self.assertEqual(self.get(PREFIX + "/999").status_code, 404)
        self.assertEqual(self.update(999, IN_PROGRESS).status_code, 404)
        for invalid in ("0", "-1", "abc"):
            self.assertEqual(self.get(PREFIX + "/" + invalid).status_code, 422)

    def test_allowed_transitions_persist_and_refresh_timestamp(self):
        for start, target in ((NEW, IN_PROGRESS), (NEW, RESOLVED), (NEW, CANCELLED),
                              (IN_PROGRESS, RESOLVED), (IN_PROGRESS, CANCELLED)):
            with self.subTest(start=start, target=target):
                with self.factory.begin() as session:
                    request = session.get(SupportRequest, 1)
                    request.status = start
                    request.updated_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
                response = self.update(1, target)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], target)
                self.assertGreater(datetime.fromisoformat(response.json()["updated_at"]).year, 2000)
                with self.factory() as session:
                    self.assertEqual(session.get(SupportRequest, 1).status, target)

    def test_invalid_transitions_are_409_and_leave_status_unchanged(self):
        for request_id, target in ((3, IN_PROGRESS), (3, CANCELLED), (4, NEW), (2, NEW)):
            with self.factory() as session:
                original = session.get(SupportRequest, request_id).status
            response = self.update(request_id, target)
            self.assertEqual(response.status_code, 409)
            with self.factory() as session:
                self.assertEqual(session.get(SupportRequest, request_id).status, original)

    def test_same_status_is_idempotent_including_terminal_statuses(self):
        for request_id in range(1, 5):
            before = self.get(f"{PREFIX}/{request_id}").json()
            response = self.update(request_id, before["status"])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["updated_at"], before["updated_at"])

    def test_bad_bodies_are_422_without_echoing_input(self):
        for body in ({"status": FAKE_KEY}, {}, {"status": None}, {"status": NEW, "admin_key": FAKE_KEY}):
            response = self.client.patch(PREFIX + "/1/status", headers=self.headers, json=body)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(FAKE_KEY, response.text)

    def test_service_and_unexpected_errors_are_sanitized_in_responses_and_logs(self):
        for error in (service.SupportRequestError(FAKE_KEY), RuntimeError(FAKE_KEY)):
            with patch.object(service, "list_support_requests", side_effect=error), \
                 self.assertLogs("app.api", level="ERROR") as logs:
                response = self.get()
            self.assertEqual(response.status_code, 500)
            self.assertEqual(response.json(), {"detail": "Internal server error."})
            self.assertNotIn(FAKE_KEY, response.text + " ".join(logs.output))

    def test_openapi_documents_header_security_and_no_cors_is_enabled(self):
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(schema["components"]["securitySchemes"]["AdminKey"]["in"], "header")
        self.assertEqual(schema["components"]["securitySchemes"]["AdminKey"]["name"], "X-Admin-Key")
        for path, methods in schema["paths"].items():
            for operation in methods.values():
                if path.startswith("/api/admin"):
                    self.assertEqual(operation["security"], [{"AdminKey": []}])
        self.assertNotIn(FAKE_KEY, json.dumps(schema))
        self.assertEqual(self.client.get("/docs").status_code, 200)
        response = self.client.get(PREFIX, headers={**self.headers, "Origin": "https://example.invalid"})
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_routes_use_worker_thread_and_contain_no_sqlalchemy_logic(self):
        threads = []
        def listing(**kwargs):
            threads.append(threading.get_ident())
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            return []
        with patch.object(service, "list_support_requests", side_effect=listing):
            self.assertEqual(self.get().status_code, 200)
        self.assertNotEqual(threads[0], threading.get_ident())
        tree = ast.parse(inspect.getsource(routes))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name.startswith(("sqlalchemy", "app.database")) for name in imports))

    def test_service_enforces_rules_without_http(self):
        with self.assertRaises(service.InvalidStatusTransition):
            service.update_support_request_status(3, IN_PROGRESS)
        with self.assertRaises(ValueError):
            service.update_support_request_status(1, "unsupported")
        with self.assertRaises(ValueError):
            service.list_support_requests(limit=101)
        self.assertFalse(can_transition(RESOLVED, IN_PROGRESS))
        self.assertFalse(can_transition(CANCELLED, NEW))
