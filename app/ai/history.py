"""Provider-independent conversation entries."""

from typing import Literal, TypedDict


class HistoryMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str
