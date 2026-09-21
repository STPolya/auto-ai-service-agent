"""Central header-only admin authentication."""

import logging
from typing import Annotated

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config.settings import get_admin_api_key
from app.security import keys_match

logger = logging.getLogger(__name__)
admin_header = APIKeyHeader(name="X-Admin-Key", scheme_name="AdminKey", auto_error=False,
                           description="Admin API key configured on the server. Supply only in this header.")


def require_admin_key(key: Annotated[str | None, Security(admin_header)]) -> None:
    if not key:
        raise HTTPException(status_code=401, detail="Admin authentication required.")
    try:
        expected = get_admin_api_key()
    except ValueError:
        logger.error("Admin authentication unavailable: category=configuration.")
        raise HTTPException(status_code=503, detail="Admin API unavailable.") from None
    if not keys_match(key, expected):
        raise HTTPException(status_code=401, detail="Invalid admin credentials.")
