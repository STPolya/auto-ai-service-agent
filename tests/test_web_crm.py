"""CRM browser flows with in-process HTTP and mocked service data only."""

from datetime import datetime, timezone
import os
import re
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.app import app
from app.services.support_request_service import InvalidStatusTransition, SupportRequestError, SupportRequestNotFound
from app.services.support_status import NEW, IN_PROGRESS, RESOLVED, CANCELLED
from app.web import dependencies as sessions

FAKE_KEY = "offline-web-admin-not-a-real-key"
FAKE_SECRET = "offline-session-signing-secret-for-unit-tests-only"
LIST_URL = "/admin/support-requests"


def item():
    now = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    return {"id": 7, "status": NEW, "summary": "Жалоба: вибрация.\nНужен осмотр.", "created_at": now,
            "updated_at": now, "conversation_id": 9,
            "user": {"id": 1, "telegram_id": 100, "username": None, "first_name": "Клиент"},
            "conversation": {"id": 9, "created_at": now},
            "messages": [{"id": 1, "role": "user", "content": "Первый вопрос", "created_at": now},
                         {"id": 2, "role": "assistant", "content": "Второй ответ", "created_at": now}],
            "vehicles": [{"id": 1, "brand": "Toyota", "model": "Corolla", "year": None, "license_plate": None}]}


class CRMWebTests(unittest.TestCase):
    def setUp(self):
        for patcher in (
            patch.dict(os.environ, {"ADMIN_API_KEY": FAKE_KEY, "WEB_SESSION_SECRET": FAKE_SECRET, "WEB_COOKIE_SECURE": "false"}),
            patch("app.config.settings.load_dotenv"),
            patch("app.database.session.get_engine", side_effect=AssertionError("No database connections")),
            patch("app.ai.client.genai.Client", side_effect=AssertionError("No Gemini calls")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        with sessions._lock:
            sessions._sessions.clear()
        self.client = TestClient(app, follow_redirects=False)
        self.addCleanup(self.client.close)
        for target, name, result in (("list_support_requests", "listing", [item()]),
                                     ("get_support_request_detail", "detail", item()),
                                     ("update_support_request_status", "update", item())):
            patcher = patch("app.web.routes.service." + target, return_value=result)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def csrf(self, response):
        return re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]

    def login(self):
        page = self.client.get("/admin/login")
        result = self.client.post("/admin/login", data={"admin_key": FAKE_KEY, "csrf_token": self.csrf(page)})
        self.assertEqual(result.status_code, 303)
        return result

    def test_login_renders_accessible_password_form_and_local_css(self):
        response = self.client.get("/admin/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn('type="password"', response.text)
        self.assertIn('lang="ru"', response.text)
        self.assertIn("AutoCare", response.text)
        self.assertIn('name="csrf_token"', response.text)
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        self.assertIn("samesite=lax", response.headers["set-cookie"].lower())
        self.assertEqual(self.client.get("/static/crm/css/crm.css").status_code, 200)

    def test_bad_login_rejected_without_reflecting_credentials(self):
        page = self.client.get("/admin/login")
        response = self.client.post("/admin/login", data={"admin_key": "bad-private-input", "csrf_token": self.csrf(page)})
        self.assertEqual(response.status_code, 401)
        self.assertIn("Неверный ключ", response.text)
        self.assertNotIn("bad-private-input", response.text)
        self.assertEqual(self.client.get(LIST_URL).status_code, 303)

    def test_success_rotates_cookie_and_never_stores_key_in_cookie(self):
        prelogin = self.client.get("/admin/login")
        old_cookie = self.client.cookies.get(sessions.COOKIE_NAME)
        self.client.post("/admin/login", data={"admin_key": FAKE_KEY, "csrf_token": self.csrf(prelogin)})
        cookie = self.client.cookies.get(sessions.COOKIE_NAME)
        self.assertNotEqual(cookie, old_cookie)
        self.assertNotIn(FAKE_KEY, cookie)
        self.assertNotIn(FAKE_SECRET, cookie)
        self.assertIsInstance(sessions.signer().loads(cookie), str)
        self.assertEqual(self.client.get(LIST_URL).status_code, 200)
        self.assertEqual(self.client.get("/admin").headers["location"], LIST_URL)

    def test_unauthenticated_pages_and_posts_redirect_to_login(self):
        for url in ("/admin", LIST_URL, LIST_URL + "/7"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/admin/login")
        for url in ("/admin/logout", LIST_URL + "/7/status"):
            self.assertEqual(self.client.post(url).status_code, 303)
        self.listing.assert_not_called()
        self.detail.assert_not_called()
        self.update.assert_not_called()

    def test_logout_revokes_session_including_replayed_cookie(self):
        self.login()
        cookie = self.client.cookies.get(sessions.COOKIE_NAME)
        token = self.csrf(self.client.get(LIST_URL))
        response = self.client.post("/admin/logout", data={"csrf_token": token})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/login")
        self.assertIsNone(self.client.cookies.get(sessions.COOKIE_NAME))
        with TestClient(app, follow_redirects=False) as replay:
            self.assertEqual(replay.get(LIST_URL, headers={"Cookie": f"{sessions.COOKIE_NAME}={cookie}"}).status_code, 303)

    def test_list_filter_pagination_empty_and_server_error(self):
        self.login()
        response = self.client.get(LIST_URL + "?status=new&limit=1&offset=2")
        self.assertEqual(response.status_code, 200)
        self.listing.assert_called_once_with(status=NEW, limit=1, offset=2)
        self.assertIn("Клиент", response.text)
        self.assertIn("Жалоба: вибрация", response.text)
        self.assertIn("offset=3", response.text)
        self.assertIn("status=new", response.text)
        self.listing.return_value = []
        self.assertIn("Обращений пока нет", self.client.get(LIST_URL).text)
        self.listing.side_effect = SupportRequestError("private SQL")
        with self.assertLogs("app.api", level="ERROR") as logs:
            response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 500)
        self.assertIn("Не удалось", response.text)
        self.assertNotIn("private SQL", response.text + str(logs.output))

    def test_detail_has_ordered_history_vehicles_and_only_valid_actions(self):
        self.login()
        for status, count in ((NEW, 3), (IN_PROGRESS, 2), (RESOLVED, 0), (CANCELLED, 0)):
            self.detail.return_value = {**item(), "status": status}
            response = self.client.get(LIST_URL + "/7")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Toyota Corolla", response.text)
            self.assertLess(response.text.index("Первый вопрос"), response.text.index("Второй ответ"))
            self.assertIn('message-user', response.text)
            self.assertIn('message-assistant', response.text)
            self.assertEqual(response.text.count('action="/admin/support-requests/7/status"'), count)

    def test_missing_detail_renders_friendly_404(self):
        self.login()
        self.detail.side_effect = SupportRequestNotFound("private detail")
        response = self.client.get(LIST_URL + "/999")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Обращение не найдено", response.text)
        self.assertNotIn("private detail", response.text)

    def test_status_post_redirect_get_and_conflict(self):
        self.login()
        token = self.csrf(self.client.get(LIST_URL + "/7"))
        response = self.client.post(LIST_URL + "/7/status", data={"status": IN_PROGRESS, "csrf_token": token})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], LIST_URL + "/7")
        self.update.assert_called_once_with(7, IN_PROGRESS)
        self.update.side_effect = InvalidStatusTransition("private details")
        response = self.client.post(LIST_URL + "/7/status", data={"status": IN_PROGRESS, "csrf_token": token})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Статус уже изменился", response.text)
        self.assertNotIn("private details", response.text)

    def test_csrf_required_for_login_logout_and_status_mutations(self):
        self.client.get("/admin/login")
        for token in (None, "wrong-token"):
            data = {"admin_key": FAKE_KEY}
            if token:
                data["csrf_token"] = token
            self.assertEqual(self.client.post("/admin/login", data=data).status_code, 403)
        self.login()
        for url in ("/admin/logout", LIST_URL + "/7/status"):
            for token in (None, "wrong-token"):
                data = {"status": RESOLVED}
                if token:
                    data["csrf_token"] = token
                self.assertEqual(self.client.post(url, data=data).status_code, 403)
        self.update.assert_not_called()
        self.assertEqual(self.client.get(LIST_URL).status_code, 200)

    def test_cookie_tampering_expiry_and_key_rotation(self):
        self.login()
        cookie = self.client.cookies.get(sessions.COOKIE_NAME)
        with TestClient(app, follow_redirects=False) as attacker:
            response = attacker.get(LIST_URL, headers={"Cookie": f"{sessions.COOKIE_NAME}=x{cookie}"})
            self.assertEqual(response.status_code, 303)
        with patch("app.web.dependencies.time.time", return_value=time.time() + sessions.SESSION_SECONDS + 1):
            self.assertEqual(self.client.get(LIST_URL).status_code, 303)
        # The simulated future has ended; start a fresh browser session.
        self.client.cookies.clear()
        self.login()
        with patch.dict(os.environ, {"ADMIN_API_KEY": "rotated-offline-key"}):
            self.assertEqual(self.client.get(LIST_URL).status_code, 303)

    def test_missing_signing_configuration_fails_closed_but_health_and_api_work(self):
        with patch.dict(os.environ, {"WEB_SESSION_SECRET": ""}):
            with self.assertLogs("app.api", level="ERROR"):
                self.assertEqual(self.client.get("/admin/login").status_code, 503)
            self.assertEqual(self.client.get("/health").status_code, 200)
            self.assertEqual(self.client.get("/api/admin/support-requests", headers={"X-Admin-Key": FAKE_KEY}).status_code, 200)

    def test_web_session_and_header_auth_remain_separate(self):
        self.assertEqual(self.client.get(LIST_URL, headers={"X-Admin-Key": FAKE_KEY}).status_code, 303)
        self.login()
        self.assertEqual(self.client.get("/api/admin/support-requests").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/support-requests", headers={"X-Admin-Key": FAKE_KEY}).status_code, 200)

    def test_https_cookie_security_and_private_page_headers(self):
        with patch.dict(os.environ, {"WEB_COOKIE_SECURE": "true"}):
            response = self.client.get("/admin/login")
        self.assertIn("secure", response.headers["set-cookie"].lower())
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-frame-options"], "DENY")

    def test_customer_text_is_escaped_and_invalid_filters_are_safe(self):
        self.login()
        malicious = '<script>alert("private")</script>'
        self.detail.return_value["summary"] = malicious
        self.detail.return_value["messages"][0]["content"] = malicious
        response = self.client.get(LIST_URL + "/7")
        self.assertNotIn(malicious, response.text)
        self.assertIn("&lt;script&gt;", response.text)
        for suffix in ("?limit=101", "?limit=oops", "?status=invalid", "?offset=-1"):
            self.assertEqual(self.client.get(LIST_URL + suffix).status_code, 422)
        self.assertNotIn(FAKE_KEY, response.text)
        self.assertNotIn(FAKE_SECRET, response.text)
