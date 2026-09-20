"""Synchronous Gemini API boundary. Call it from a worker thread."""

import logging
import time

import httpx
from google import genai
from google.genai import errors, types

from app.ai.prompts import RESPONSE_SCHEMA
from app.ai.history import HistoryMessage
from app.config.settings import get_gemini_api_key

PRIMARY_MODEL = "gemini-3.8-flash"
FALLBACK_MODEL = "gemini-3.5-flash"
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
RETRY_DELAYS = (1, 2)
logger = logging.getLogger(__name__)


class GeminiError(RuntimeError):
    """Sanitized provider failure; never carry the SDK exception to callers."""


def _generate_with_retries(client, model: str, user_prompt: str | list[types.Content], config):
    """Retry one model; terminal API errors stay inside this client boundary."""
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            return client.models.generate_content(model=model, contents=user_prompt, config=config)
        except errors.APIError as error:
            status = error.code if type(error.code) is int else None
            temporary = status in RETRYABLE_STATUS_CODES
            logger.warning(
                "Gemini provider failed: model=%s type=%s status=%s attempt=%d/3",
                model, "temporary_api_error" if temporary else "non_retryable_api_error",
                status if status is not None else "unknown", attempt + 1,
            )
            if not temporary or attempt == len(RETRY_DELAYS):
                raise
            time.sleep(RETRY_DELAYS[attempt])


def generate_response(system_prompt: str, user_prompt: list[HistoryMessage] | str) -> str:
    model = PRIMARY_MODEL
    try:
        if not isinstance(user_prompt, str):
            if any(entry["role"] not in ("user", "assistant") for entry in user_prompt):
                raise ValueError("Invalid history role")
            user_prompt = [
                types.Content(
                    role="model" if entry["role"] == "assistant" else "user",
                    parts=[types.Part(text=entry["content"])],
                )
                for entry in user_prompt
            ]
        api_key = get_gemini_api_key()
        with genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=30000, retry_options=types.HttpRetryOptions(attempts=1),
        )) as client:
            config = types.GenerateContentConfig(
                system_instruction=system_prompt, response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA, max_output_tokens=2048,
            )
            try:
                response = _generate_with_retries(client, model, user_prompt, config)
            except errors.APIError as error:
                # Only a terminal 503 from primary generation activates failover.
                if type(error.code) is not int or error.code != 503:
                    raise
                logger.warning(
                    "Gemini fallback activated: model=%s fallback_model=%s "
                    "type=primary_unavailable status=503 attempt=3/3",
                    PRIMARY_MODEL, FALLBACK_MODEL,
                )
                model = FALLBACK_MODEL
                response = _generate_with_retries(client, model, user_prompt, config)
            text = response.text
        # Empty/blocked responses are errors, not successful diagnoses.
        if not isinstance(text, str) or not text.strip() or api_key in text:
            logger.warning("Gemini provider failed: model=%s type=invalid_response", model)
            raise GeminiError("Diagnostic assistant is temporarily unavailable.")
        return text
    except errors.APIError:
        raise GeminiError("Diagnostic assistant is temporarily unavailable.") from None
    except (httpx.HTTPError, OSError):
        logger.warning("Gemini provider failed: model=%s type=transport_error", model)
        raise GeminiError("Diagnostic assistant is temporarily unavailable.") from None
    except ValueError:
        logger.warning("Gemini provider failed: model=%s type=configuration_or_response_error", model)
        raise GeminiError("Diagnostic assistant is temporarily unavailable.") from None
