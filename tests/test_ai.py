"""AI tests mock the SDK; no real API key, .env, or Gemini requests."""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import httpx
from google.genai import errors

from app.ai.client import GeminiError, PRIMARY_MODEL, FALLBACK_MODEL, generate_response
from app.ai.prompts import RESPONSE_SCHEMA, SYSTEM_PROMPT, build_user_prompt
from app.ai.service import DiagnosticError, DiagnosticInputError, diagnose_problem
from app.config.settings import get_gemini_api_key

PAYLOAD = {
    "possible_causes": ["Возможно, изношены тормозные колодки; нужна проверка."],
    "questions": ["Когда возникает шум?"],
    "recommendations": ["Обратитесь на профессиональный осмотр."],
}
FAKE_KEY = "unit-test-key-not-a-real-credential"


class GeminiModelTests(unittest.TestCase):
    def test_concrete_model_names(self):
        self.assertEqual(PRIMARY_MODEL, "gemini-3.8-flash")
        self.assertEqual(FALLBACK_MODEL, "gemini-3.5-flash")


class GeminiClientTests(unittest.TestCase):
    def setUp(self):
        for patcher in (
            patch.dict(os.environ, {"GEMINI_API_KEY": FAKE_KEY}),
            patch("app.config.settings.load_dotenv"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("app.ai.client.genai.Client")
        self.constructor = patcher.start()
        self.addCleanup(patcher.stop)
        self.client = self.constructor.return_value.__enter__.return_value
        self.client.models.generate_content.return_value = SimpleNamespace(text=json.dumps(PAYLOAD))
        patcher = patch("app.ai.client.time.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_sdk_receives_configured_key_prompt_schema_and_timeout(self):
        raw = generate_response(SYSTEM_PROMPT, "problem")
        self.assertEqual(json.loads(raw), PAYLOAD)
        self.assertEqual(self.constructor.call_args.kwargs["api_key"], FAKE_KEY)
        self.assertEqual(self.constructor.call_args.kwargs["http_options"].timeout, 30000)
        self.assertEqual(self.constructor.call_args.kwargs["http_options"].retry_options.attempts, 1)
        args = self.client.models.generate_content.call_args.kwargs
        self.assertEqual(args["model"], PRIMARY_MODEL)
        self.assertEqual(args["contents"], "problem")
        self.assertEqual(args["config"].system_instruction, SYSTEM_PROMPT)
        self.assertEqual(args["config"].response_mime_type, "application/json")
        self.constructor.return_value.__exit__.assert_called_once()
        self.client.models.generate_content.assert_called_once()
        self.sleep.assert_not_called()

    def test_missing_key_is_safe(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY is required"):
                get_gemini_api_key()
            with self.assertRaises(GeminiError):
                generate_response("system", "problem")
        self.constructor.assert_not_called()

    def test_history_is_sent_as_native_roles_and_reused_during_fallback(self):
        history = [{"role": "user", "content": "Вибрация при торможении"},
                   {"role": "assistant", "content": "На какой скорости?"},
                   {"role": "user", "content": "После 80 км/ч"}]
        self.client.models.generate_content.side_effect = [errors.APIError(503, {})] * 3 + [
            SimpleNamespace(text=json.dumps(PAYLOAD)),
        ]
        with self.assertLogs("app.ai.client", level="WARNING") as logs:
            generate_response(SYSTEM_PROMPT, history)
        calls = self.client.models.generate_content.call_args_list
        contents = calls[0].kwargs["contents"]
        self.assertEqual([entry.role for entry in contents], ["user", "model", "user"])
        self.assertEqual([entry.parts[0].text for entry in contents], [entry["content"] for entry in history])
        self.assertTrue(all(request.kwargs["contents"] is contents for request in calls))
        for entry in history:
            self.assertNotIn(entry["content"], " ".join(logs.output))

    def test_api_and_network_errors_do_not_expose_key_or_log_it(self):
        for error in (errors.APIError(503, {"error": {"message": FAKE_KEY}}), httpx.ReadTimeout(FAKE_KEY)):
            self.client.models.generate_content.side_effect = error
            with self.assertLogs("app.ai.client", level="WARNING") as logs:
                with self.assertRaises(GeminiError) as result:
                    generate_response("system", "problem")
            self.assertNotIn(FAKE_KEY, " ".join(logs.output))
            self.assertNotIn("problem", " ".join(logs.output))
            self.assertNotIn(FAKE_KEY, str(result.exception))
            self.assertIsNone(result.exception.__cause__)

    def test_empty_blocked_or_key_containing_response_is_rejected(self):
        for text in (None, "", "  ", FAKE_KEY):
            self.client.models.generate_content.return_value = SimpleNamespace(text=text)
            with self.assertRaises(GeminiError):
                generate_response("system", "problem")

    def test_both_models_exhaust_503_retries_then_fail_safely(self):
        self.client.models.generate_content.side_effect = errors.APIError(503, {"error": {"message": FAKE_KEY}})
        with self.assertLogs("app.ai.client", level="WARNING") as logs:
            with self.assertRaises(GeminiError):
                generate_response("system", "private-problem")
        self.assertEqual(self.client.models.generate_content.call_count, 6)
        self.assertEqual([c.kwargs["model"] for c in self.client.models.generate_content.call_args_list],
                         [PRIMARY_MODEL] * 3 + [FALLBACK_MODEL] * 3)
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2), call(1), call(2)])
        self.assertEqual(len(logs.output), 7)
        self.assertIn("fallback activated", logs.output[3])
        self.assertIn("type=temporary_api_error status=503 attempt=3/3", logs.output[-1])
        self.assertNotIn(FAKE_KEY, " ".join(logs.output))
        self.assertNotIn("private-problem", " ".join(logs.output))
        self.constructor.return_value.__exit__.assert_called_once()

    def test_retry_succeeds_after_first_failure(self):
        self.client.models.generate_content.side_effect = [
            errors.APIError(503, {}), SimpleNamespace(text=json.dumps(PAYLOAD)),
        ]
        with self.assertLogs("app.ai.client", level="WARNING"):
            result = generate_response("system", "problem")
        self.assertEqual(json.loads(result), PAYLOAD)
        self.assertEqual(self.client.models.generate_content.call_count, 2)
        self.assertTrue(all(c.kwargs["model"] == PRIMARY_MODEL for c in self.client.models.generate_content.call_args_list))
        self.sleep.assert_called_once_with(1)

    def test_retry_can_succeed_on_third_attempt(self):
        self.client.models.generate_content.side_effect = [
            errors.APIError(503, {}), errors.APIError(502, {}), SimpleNamespace(text=json.dumps(PAYLOAD)),
        ]
        with self.assertLogs("app.ai.client", level="WARNING"):
            self.assertEqual(json.loads(generate_response("system", "problem")), PAYLOAD)
        self.assertEqual(self.client.models.generate_content.call_count, 3)
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2)])

    def test_authentication_and_invalid_requests_are_not_retried(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                self.client.models.generate_content.reset_mock()
                self.sleep.reset_mock()
                self.client.models.generate_content.side_effect = errors.APIError(status, {"error": {"message": FAKE_KEY}})
                with self.assertLogs("app.ai.client", level="WARNING") as logs:
                    with self.assertRaises(GeminiError):
                        generate_response("system", "problem")
                self.client.models.generate_content.assert_called_once()
                self.assertEqual(self.client.models.generate_content.call_args.kwargs["model"], PRIMARY_MODEL)
                self.sleep.assert_not_called()
                self.assertIn(f"non_retryable_api_error status={status}", logs.output[0])
                self.assertNotIn(FAKE_KEY, " ".join(logs.output))

    def test_other_transient_api_statuses_are_retried(self):
        for status in (408, 429, 500, 502, 504):
            with self.subTest(status=status):
                self.client.models.generate_content.reset_mock()
                self.sleep.reset_mock()
                self.client.models.generate_content.side_effect = [
                    errors.APIError(status, {}), SimpleNamespace(text=json.dumps(PAYLOAD)),
                ]
                with self.assertLogs("app.ai.client", level="WARNING"):
                    generate_response("system", "problem")
                self.assertEqual(self.client.models.generate_content.call_count, 2)
                self.sleep.assert_called_once_with(1)

    def test_authentication_failure_after_503_stops_retries(self):
        self.client.models.generate_content.side_effect = [errors.APIError(503, {}), errors.APIError(403, {})]
        with self.assertLogs("app.ai.client", level="WARNING"):
            with self.assertRaises(GeminiError):
                generate_response("system", "problem")
        self.assertEqual(self.client.models.generate_content.call_count, 2)
        self.sleep.assert_called_once_with(1)

    def test_fallback_success_returns_response_with_same_request_configuration(self):
        response = json.dumps({**PAYLOAD, "questions": ["Когда появился звук?"]})
        self.client.models.generate_content.side_effect = [
            errors.APIError(503, {}), errors.APIError(503, {}), errors.APIError(503, {}),
            SimpleNamespace(text=response),
        ]
        with self.assertLogs("app.ai.client", level="WARNING") as logs:
            self.assertEqual(generate_response(SYSTEM_PROMPT, "private-problem"), response)
        calls = self.client.models.generate_content.call_args_list
        self.assertEqual([c.kwargs["model"] for c in calls], [PRIMARY_MODEL] * 3 + [FALLBACK_MODEL])
        for request in calls:
            self.assertEqual(request.kwargs["contents"], "private-problem")
            self.assertIs(request.kwargs["config"], calls[0].kwargs["config"])
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2)])
        self.assertIn(PRIMARY_MODEL, logs.output[-1])
        self.assertIn(FALLBACK_MODEL, logs.output[-1])
        self.assertNotIn("private-problem", " ".join(logs.output))

    def test_fallback_uses_same_transient_retry_policy(self):
        self.client.models.generate_content.side_effect = [errors.APIError(503, {})] * 3 + [
            errors.APIError(502, {}), SimpleNamespace(text=json.dumps(PAYLOAD)),
        ]
        with self.assertLogs("app.ai.client", level="WARNING"):
            self.assertEqual(json.loads(generate_response("system", "problem")), PAYLOAD)
        self.assertEqual([c.kwargs["model"] for c in self.client.models.generate_content.call_args_list],
                         [PRIMARY_MODEL] * 3 + [FALLBACK_MODEL] * 2)
        self.assertEqual(self.sleep.call_args_list, [call(1), call(2), call(1)])

    def test_non_503_final_transient_status_does_not_activate_fallback(self):
        for status in (408, 429, 500, 502, 504):
            self.client.models.generate_content.reset_mock()
            self.sleep.reset_mock()
            self.client.models.generate_content.side_effect = [errors.APIError(503, {})] * 2 + [errors.APIError(status, {})]
            with self.assertLogs("app.ai.client", level="WARNING") as logs:
                with self.assertRaises(GeminiError):
                    generate_response("system", "problem")
            self.assertEqual([c.kwargs["model"] for c in self.client.models.generate_content.call_args_list], [PRIMARY_MODEL] * 3)
            self.assertFalse(any("fallback activated" in entry for entry in logs.output))

    def test_invalid_output_or_local_failure_never_activates_fallback(self):
        for value in (None, "", "not json"):
            self.client.models.generate_content.reset_mock()
            self.client.models.generate_content.return_value = SimpleNamespace(text=value)
            with self.assertRaises(DiagnosticError):
                diagnose_problem("problem")
            self.client.models.generate_content.assert_called_once()
            self.assertEqual(self.client.models.generate_content.call_args.kwargs["model"], PRIMARY_MODEL)
        for error in (ValueError(FAKE_KEY), httpx.ReadTimeout(FAKE_KEY)):
            self.client.models.generate_content.reset_mock()
            self.client.models.generate_content.side_effect = error
            with self.assertRaises(GeminiError):
                generate_response("system", "problem")
            self.client.models.generate_content.assert_called_once()
        self.sleep.assert_not_called()


class AIServiceTests(unittest.TestCase):
    def test_service_passes_plain_history_without_duplicate_current_message(self):
        history = [{"role": "user", "content": "Вибрация"},
                   {"role": "assistant", "content": "Когда?"},
                   {"role": "user", "content": "При торможении"}]
        with patch("app.ai.service.generate_response", return_value=json.dumps(PAYLOAD)) as client:
            diagnose_problem(history)
        client.assert_called_once_with(SYSTEM_PROMPT, history)

    def test_prompt_separates_description_from_system_rules(self):
        self.assertEqual(build_user_prompt("Шум при торможении"), "Описание проблемы от пользователя:\nШум при торможении")
        for text in ("Russian", "guaranteed diagnosis", "brake", "steering", "fuel leak", "emergency", "dangerous"):
            self.assertIn(text, SYSTEM_PROMPT)
        self.assertEqual(set(RESPONSE_SCHEMA["required"]), set(PAYLOAD))

    def test_service_calls_client_and_formats_russian_sections(self):
        with patch("app.ai.service.generate_response", return_value=json.dumps(PAYLOAD)) as client:
            result = diagnose_problem("  Шум при торможении  ")
        client.assert_called_once_with(SYSTEM_PROMPT, build_user_prompt("Шум при торможении"))
        for heading in ("Возможные причины:", "Вопросы:", "Рекомендация:"):
            self.assertIn(heading, result)
        self.assertIn(PAYLOAD["recommendations"][0], result)

    def test_empty_and_oversized_input_does_not_call_gemini(self):
        with patch("app.ai.service.generate_response") as client:
            for text in ("", "  ", "x" * 3001):
                with self.assertRaises(DiagnosticInputError):
                    diagnose_problem(text)
        client.assert_not_called()

    def test_provider_failure_is_sanitized(self):
        with patch("app.ai.service.generate_response", side_effect=GeminiError(FAKE_KEY)):
            with self.assertRaises(DiagnosticError) as result:
                diagnose_problem("problem")
        self.assertNotIn(FAKE_KEY, str(result.exception))

    def test_malformed_missing_or_oversized_output_is_not_success(self):
        for raw in ("not json", "[]", "{}", json.dumps({**PAYLOAD, "questions": []}),
                    json.dumps({**PAYLOAD, "questions": ["x" * 301]})):
            with patch("app.ai.service.generate_response", return_value=raw):
                with self.assertRaises(DiagnosticError):
                    diagnose_problem("problem")
