"""Offline regressions for the repository security review."""

import asyncio
import io
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from app.api.errors import MAX_REQUEST_BODY_BYTES, RequestBodyLimitMiddleware
from app.bot.secure_logging import configure_telegram_logging
from app.rag.ingestion import IngestionError, load_documents
from app.web import dependencies as sessions


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        with sessions._lock:
            sessions._sessions.clear()

    def tearDown(self):
        with sessions._lock:
            sessions._sessions.clear()

    def test_anonymous_session_flood_cannot_evict_operator(self):
        with patch.object(sessions, "MAX_SESSIONS", 3):
            operator = sessions.new_session("offline-test-key")
            for _ in range(10):
                sessions.new_session()
            self.assertIn(operator.id, sessions._sessions)
            self.assertEqual(len(sessions._sessions), 3)

    def test_full_authenticated_store_rejects_new_session_safely(self):
        with patch.object(sessions, "MAX_SESSIONS", 1):
            operator = sessions.new_session("offline-test-key")
            with self.assertRaises(sessions.WebError) as caught:
                sessions.new_session()
            self.assertEqual(caught.exception.status_code, 503)
            self.assertIn(operator.id, sessions._sessions)

    def test_csrf_is_bound_to_session(self):
        first, second = sessions.new_session(), sessions.new_session()
        with self.assertRaises(sessions.WebError):
            sessions.check_csrf(first, second.csrf)
        sessions.check_csrf(first, first.csrf)

    def test_ingestion_rejects_link_before_reading_content(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "linked.md"
            path.write_text("# offline fixture", encoding="utf-8")
            # Portable on Windows without symlink privileges; exercise the gate.
            with patch.object(Path, "is_symlink", return_value=True), \
                 patch.object(Path, "read_text", side_effect=AssertionError("Must not read linked source")):
                with self.assertRaises(IngestionError):
                    load_documents(Path(folder))

    def test_ingestion_ignores_non_markdown_and_hidden_env(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "guide.md").write_text("# Guide\nIntended content", encoding="utf-8")
            (root / ".env").write_text("OFFLINE_FIXTURE=do-not-ingest", encoding="utf-8")
            (root / "backup.txt").write_text("do-not-ingest", encoding="utf-8")
            documents = load_documents(root)
            self.assertEqual(list(documents), ["guide.md"])
            self.assertEqual(documents["guide.md"][0].content, "Intended content")

    def test_aiogram_exception_arguments_and_traceback_are_sanitized(self):
        for name in ("aiogram.event", "aiogram.dispatcher"):
            logger = logging.getLogger(name)
            old_filters, old_level = list(logger.filters), logger.level
            output = io.StringIO()
            handler = logging.StreamHandler(output)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            try:
                configure_telegram_logging()
                configure_telegram_logging()
                try:
                    raise RuntimeError("offline-private-payload")
                except RuntimeError as error:
                    logger.exception("Raw provider error: %s", error)
                logger.error("Polling failed: %s", "offline-private-payload")
                logger.info("Polling started")
                self.assertNotIn("offline-private-payload", output.getvalue())
                self.assertNotIn("Traceback", output.getvalue())
                self.assertIn("category=transport_or_update", output.getvalue())
                self.assertIn("Polling started", output.getvalue())
            finally:
                logger.removeHandler(handler)
                logger.filters[:] = old_filters
                logger.setLevel(old_level)

    def test_oversized_body_rejected_before_form_parsing_even_without_length(self):
        async def check():
            downstream, send = AsyncMock(), AsyncMock()
            receive = AsyncMock(side_effect=[
                {"type": "http.request", "body": b"x" * MAX_REQUEST_BODY_BYTES, "more_body": True},
                {"type": "http.request", "body": b"x", "more_body": False},
            ])
            await RequestBodyLimitMiddleware(downstream)(
                {"type": "http", "method": "POST", "path": "/admin/login", "headers": []}, receive, send)
            downstream.assert_not_awaited()
            self.assertEqual(send.call_args_list[0].args[0]["status"], 413)
        asyncio.run(check())

    def test_small_chunked_body_is_preserved(self):
        async def check():
            async def downstream(scope, receive, send):
                message = await receive()
                self.assertEqual(message["body"], b"status=new")
                self.assertFalse(message["more_body"])
            receive = AsyncMock(side_effect=[
                {"type": "http.request", "body": b"status=", "more_body": True},
                {"type": "http.request", "body": b"new", "more_body": False},
            ])
            await RequestBodyLimitMiddleware(downstream)(
                {"type": "http", "method": "PATCH", "path": "/api/admin/support-requests/1/status"},
                receive, AsyncMock())
        asyncio.run(check())
