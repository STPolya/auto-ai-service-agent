"""Support-request domain states; operator workflow is a future client."""

NEW = "new"
IN_PROGRESS = "in_progress"
RESOLVED = "resolved"
CANCELLED = "cancelled"
ACTIVE_STATUSES = (NEW, IN_PROGRESS)
ALLOWED_STATUSES = (*ACTIVE_STATUSES, RESOLVED, CANCELLED)
