"""Safe HTTP error boundary: never propagate raw failures to ASGI server logs."""

import logging

from fastapi.responses import JSONResponse, HTMLResponse

logger = logging.getLogger(__name__)


class SanitizedErrorsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False
        finished = False

        async def safe_send(message):
            nonlocal started, finished
            if message["type"] == "http.response.start":
                started = True
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True
            await send(message)

        try:
            await self.app(scope, receive, safe_send)
        except Exception:
            # Catch at the HTTP boundary, not inside business logic. Re-raising
            # would make Uvicorn log the original exception and possibly secrets.
            logger.error("Admin HTTP request failed: category=unexpected.")
            if not started:
                if scope["path"] == "/admin" or scope["path"].startswith("/admin/"):
                    response = HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><title>AutoCare CRM</title><h1>Не удалось загрузить страницу</h1><p>Попробуйте позже.</p><a href="/admin/support-requests">К обращениям</a></html>', status_code=500, headers={"Cache-Control": "no-store"})
                else:
                    response = JSONResponse(status_code=500, content={"detail": "Internal server error."})
                await response(scope, receive, send)
            elif not finished:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
