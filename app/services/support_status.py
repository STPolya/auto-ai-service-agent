"""Support-request states and transitions shared by every application client."""

from enum import Enum


class SupportStatus(str, Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


# Keep existing callers' string constants compatible.
NEW = SupportStatus.NEW.value
IN_PROGRESS = SupportStatus.IN_PROGRESS.value
RESOLVED = SupportStatus.RESOLVED.value
CANCELLED = SupportStatus.CANCELLED.value
ACTIVE_STATUSES = (NEW, IN_PROGRESS)
ALLOWED_STATUSES = tuple(status.value for status in SupportStatus)
STATUS_TRANSITIONS = {
    NEW: frozenset((IN_PROGRESS, RESOLVED, CANCELLED)),
    IN_PROGRESS: frozenset((RESOLVED, CANCELLED)),
    RESOLVED: frozenset(),
    CANCELLED: frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    return current in ALLOWED_STATUSES and (current == target or target in STATUS_TRANSITIONS[current])
