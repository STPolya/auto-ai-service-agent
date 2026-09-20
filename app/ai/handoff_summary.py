"""Bounded operator summaries with a deterministic provider-independent fallback."""

import json
import logging

from app.ai.client import GeminiError, generate_response
from app.ai.handoff_prompts import SUMMARY_PROMPT, SUMMARY_SCHEMA
from app.ai.history import HistoryMessage

MAX_HANDOFF_MESSAGES = 30
MAX_HANDOFF_MESSAGE_CHARS = 3000
MAX_SUMMARY_CHARS = 3000
logger = logging.getLogger(__name__)


def fallback_summary(history: list[HistoryMessage]) -> str:
    if not history:
        return "Диалог пока не содержит диагностических сообщений. Причину обращения и данные автомобиля нужно уточнить."
    users = [entry["content"] for entry in history if entry["role"] == "user"]
    assistants = [entry["content"] for entry in history if entry["role"] == "assistant"]
    lines = ["Краткая выписка из диалога; сведения приведены без диагностических выводов."]
    if users:
        lines.append("Первое сообщение клиента: " + " ".join(users[0].split())[:600])
        # Exclude the first user turn before selecting recent clarifications.
        clarifications = users[1:][-3:]
        if clarifications:
            lines.append("Последние уточнения клиента:")
            lines.extend("- " + " ".join(content.split())[:350] for content in clarifications)
    else:
        lines.append("Сообщения клиента отсутствуют в доступной части диалога.")
    if assistants:
        lines.append("Последний ответ AI (обсуждение, не установленный диагноз): "
                     + " ".join(assistants[-1].split())[:700])
    lines.append("Оператору: уточнить недостающие сведения и проверить вопросы безопасности по исходной истории.")
    return "\n".join(lines)[:MAX_SUMMARY_CHARS]


def summarize_handoff(history: list[HistoryMessage]) -> str:
    # Defensive bounds for future API clients as well as the database caller.
    if len(history) > MAX_HANDOFF_MESSAGES:
        first = MAX_HANDOFF_MESSAGES // 3
        history = history[:first] + history[-(MAX_HANDOFF_MESSAGES - first):]
    bounded = [{"role": entry["role"], "content": entry["content"][:MAX_HANDOFF_MESSAGE_CHARS]}
               for entry in history]
    if not bounded:
        return fallback_summary(bounded)
    try:
        raw = generate_response(SUMMARY_PROMPT, json.dumps(bounded, ensure_ascii=False), response_schema=SUMMARY_SCHEMA)
        payload = json.loads(raw)
        summary = payload.get("summary") if isinstance(payload, dict) else None
        if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_SUMMARY_CHARS:
            raise ValueError("Invalid summary response")
        return summary.strip()
    except (GeminiError, ValueError):
        logger.warning("Handoff summary unavailable: category=provider_or_response; using local fallback.")
        return fallback_summary(bounded)
