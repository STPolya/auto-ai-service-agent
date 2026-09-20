"""Bounded conversation context in, one concise Russian answer out."""

import json

from app.ai.client import GeminiError, generate_response
from app.ai.history import HistoryMessage
from app.ai.prompts import SYSTEM_PROMPT, build_user_prompt

MAX_PROBLEM_LENGTH = 3000
SECTIONS = (
    ("possible_causes", "Возможные причины"),
    ("questions", "Вопросы"),
    ("recommendations", "Рекомендация"),
)


class DiagnosticInputError(ValueError):
    """Safe explanation of invalid input."""


class DiagnosticError(RuntimeError):
    """Safe failure for application callers."""


def validate_problem(problem: str) -> str:
    problem = problem.strip()
    if not problem or len(problem) > MAX_PROBLEM_LENGTH:
        raise DiagnosticInputError(f"Опишите проблему текстом от 1 до {MAX_PROBLEM_LENGTH} символов.")
    return problem


def diagnose_problem(history: list[HistoryMessage] | str) -> str:
    # Keep the single-description entry point usable for non-conversation callers.
    if isinstance(history, str):
        history = build_user_prompt(validate_problem(history))
    elif not history or history[-1]["role"] != "user":
        raise DiagnosticInputError("Добавьте сообщение с описанием проблемы.")
    try:
        raw = generate_response(SYSTEM_PROMPT, history)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        sections = []
        for key, heading in SECTIONS:
            items = payload.get(key)
            if not isinstance(items, list) or not 1 <= len(items) <= 3:
                raise ValueError
            if any(not isinstance(item, str) or not item.strip() or len(item.strip()) > 300 for item in items):
                raise ValueError
            sections.append(heading + ":\n" + "\n".join("- " + item.strip() for item in items))
        return "\n\n".join(sections)
    except (GeminiError, ValueError):
        # Invalid/truncated provider output must not appear as successful advice.
        raise DiagnosticError("Diagnostic assistant is temporarily unavailable.") from None
