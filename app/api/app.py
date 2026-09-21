"""ASGI entry point: python -m uvicorn app.api.app:app --reload --no-access-log."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.errors import SanitizedErrorsMiddleware
from app.api.routes import router
from app.api.schemas import HealthResponse
from app.services.support_request_service import InvalidStatusTransition, SupportRequestError, SupportRequestNotFound
from app.web.routes import router as web_router
from app.web.dependencies import LoginRequired, WebError
from app.web.view_models import WEB_DIRECTORY, error_page, is_web_path

logger = logging.getLogger(__name__)
app = FastAPI(title="AutoCare CRM/Admin API", version="0.1.0", debug=False)
app.add_middleware(SanitizedErrorsMiddleware)
app.include_router(router)
app.mount("/static/crm", StaticFiles(directory=str(WEB_DIRECTORY / "static")), name="crm_static")
app.include_router(web_router)


@app.exception_handler(LoginRequired)
async def login_required(request: Request, error: LoginRequired):
    return RedirectResponse("/admin/login", status_code=303)


@app.exception_handler(WebError)
async def web_error(request: Request, error: WebError):
    if error.status_code >= 500:
        logger.error("CRM page unavailable: category=configuration.")
    return error_page(request, error.status_code, error.message)


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health() -> HealthResponse:
    return HealthResponse()


@app.exception_handler(SupportRequestNotFound)
async def not_found(request: Request, error: SupportRequestNotFound):
    if is_web_path(request.url.path):
        return error_page(request, 404, "Такого обращения нет. Вернитесь к списку и выберите другое.")
    return JSONResponse(status_code=404, content={"detail": "Support request not found."})


@app.exception_handler(InvalidStatusTransition)
async def transition_conflict(request: Request, error: InvalidStatusTransition):
    if is_web_path(request.url.path):
        return error_page(request, 409, "Переход недоступен: возможно, обращение уже обработано. Вернитесь к списку и откройте его снова.")
    return JSONResponse(status_code=409, content={"detail": "Status transition is not allowed."})


@app.exception_handler(SupportRequestError)
async def service_failure(request: Request, error: SupportRequestError):
    logger.error("Admin HTTP request failed: category=service.")
    if is_web_path(request.url.path):
        return error_page(request, 500, "Не удалось получить данные. Попробуйте обновить страницу позже.")
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


@app.exception_handler(RequestValidationError)
async def invalid_input(request: Request, error: RequestValidationError):
    # Default validation responses may echo arbitrary submitted input.
    if is_web_path(request.url.path):
        return error_page(request, 422, "Проверьте параметры формы или вернитесь к списку обращений.")
    return JSONResponse(status_code=422, content={"detail": "Invalid request input."})
