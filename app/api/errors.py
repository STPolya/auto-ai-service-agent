"""Safe HTTP error boundary: never propagate raw failures to ASGI server logs."""

import logging

from fastapi.responses import JSONResponse, HTMLResponse

logger = logging.getLogger(__name__)
MAX_REQUEST_BODY_BYTES = 64 * 1024


class RequestBodyLimitMiddleware:
    """Bound small CRM forms/JSON before framework parsing, including chunked input."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_REQUEST_BODY_BYTES:
                if scope["path"].startswith("/admin/"):
                    response = HTMLResponse("<p>Форма слишком большая. Вернитесь назад и сократите ввод.</p>",
                                            status_code=413, headers={"Cache-Control": "no-store"})
                else:
                    response = JSONResponse({"detail": "Request body too large."}, status_code=413)
                await response(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)


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
                if scope["path"] == "/admin" or scope["path"].startswith(("/admin/", "/api/admin/")):
                    message = dict(message)
                    headers = [(key, value) for key, value in message.get("headers", [])
                               if key.lower() != b"cache-control"]
                    message["headers"] = headers + [(b"cache-control", b"no-store")]
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
                    response = JSONResponse(status_code=500, content={"detail": "Internal server error."},
                                            headers={"Cache-Control": "no-store"})
                await response(scope, receive, send)
            elif not finished:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
