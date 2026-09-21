"""ASGI entry point: python -m uvicorn app.api.app:app --reload --no-access-log."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.errors import SanitizedErrorsMiddleware
from app.api.routes import router
from app.api.schemas import HealthResponse
from app.services.support_request_service import InvalidStatusTransition, SupportRequestError, SupportRequestNotFound

logger = logging.getLogger(__name__)
app = FastAPI(title="AutoCare CRM/Admin API", version="0.1.0", debug=False)
app.add_middleware(SanitizedErrorsMiddleware)
app.include_router(router)


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health() -> HealthResponse:
    return HealthResponse()


@app.exception_handler(SupportRequestNotFound)
async def not_found(request: Request, error: SupportRequestNotFound):
    return JSONResponse(status_code=404, content={"detail": "Support request not found."})


@app.exception_handler(InvalidStatusTransition)
async def transition_conflict(request: Request, error: InvalidStatusTransition):
    return JSONResponse(status_code=409, content={"detail": "Status transition is not allowed."})


@app.exception_handler(SupportRequestError)
async def service_failure(request: Request, error: SupportRequestError):
    logger.error("Admin HTTP request failed: category=service.")
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


@app.exception_handler(RequestValidationError)
async def invalid_input(request: Request, error: RequestValidationError):
    # Default validation responses may echo arbitrary submitted input.
    return JSONResponse(status_code=422, content={"detail": "Invalid request input."})
