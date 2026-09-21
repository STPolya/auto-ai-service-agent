"""HTML/form transport; services own all queries and state transitions."""

import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.config.settings import get_admin_api_key
from app.security import keys_match
from app.services import support_request_service as service
from app.services.support_status import ALLOWED_STATUSES
from app.web.dependencies import (BrowserSession, COOKIE_NAME, LoginRequired, WebError, check_csrf,
                                  new_session, read_session, require_session, revoke, set_cookie, signer)
from app.web.view_models import render

router = APIRouter(prefix="/admin", include_in_schema=False)
logger = logging.getLogger(__name__)
Session = Annotated[BrowserSession, Depends(require_session)]


@router.get("/login")
def login_page(request: Request):
    session = read_session(request)
    if session and session.key_fingerprint:
        try:
            require_session(request)
            return RedirectResponse("/admin/support-requests", status_code=303)
        except LoginRequired:
            session = None
    signer()  # Fail closed before showing an unusable login form.
    session = session or new_session()
    response = render(request, "login.html", session=session)
    set_cookie(response, session)
    return response


@router.post("/login")
def login(request: Request, admin_key: Annotated[str, Form()] = "", csrf_token: Annotated[str, Form()] = ""):
    session = read_session(request)
    check_csrf(session, csrf_token)
    try:
        expected = get_admin_api_key()
    except ValueError:
        logger.error("CRM login unavailable: category=configuration.")
        raise WebError(503, "Вход временно недоступен. Обратитесь к администратору.") from None
    if not keys_match(admin_key, expected):
        return render(request, "login.html", session=session, status_code=401,
                      error="Неверный ключ доступа. Проверьте его и попробуйте ещё раз.")
    revoke(session)
    authenticated = new_session(expected)
    response = RedirectResponse("/admin/support-requests", status_code=303)
    set_cookie(response, authenticated)
    return response


@router.post("/logout")
def logout(request: Request, session: Session, csrf_token: Annotated[str, Form()] = ""):
    check_csrf(session, csrf_token)
    revoke(session)
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/admin", httponly=True, samesite="lax")
    return response


@router.get("")
@router.get("/")
def home(session: Session):
    return RedirectResponse("/admin/support-requests", status_code=303)


@router.get("/support-requests")
def support_requests(request: Request, session: Session, status: str = "", limit: int = 20, offset: int = 0):
    if (status and status not in ALLOWED_STATUSES) or not 1 <= limit <= service.MAX_ADMIN_PAGE_SIZE or offset < 0:
        raise WebError(422, "Проверьте фильтр и параметры страницы.")
    items = service.list_support_requests(status=status or None, limit=limit, offset=offset)
    def page_link(start):
        return "/admin/support-requests?" + urlencode({"status": status, "limit": limit, "offset": start})
    return render(request, "support_requests.html", session=session, items=items, selected_status=status,
                  limit=limit, offset=offset, previous=page_link(max(0, offset-limit)) if offset else None,
                  following=page_link(offset+limit) if len(items) == limit else None)


@router.get("/support-requests/{request_id}")
def detail(request: Request, request_id: int, session: Session):
    item = service.get_support_request_detail(request_id)
    return render(request, "support_request_detail.html", session=session, item=item)


@router.post("/support-requests/{request_id}/status")
def change_status(request: Request, request_id: int, session: Session,
                  status: Annotated[str, Form()] = "", csrf_token: Annotated[str, Form()] = ""):
    check_csrf(session, csrf_token)
    if status not in ALLOWED_STATUSES:
        raise WebError(422, "Выберите допустимый статус обращения.")
    service.update_support_request_status(request_id, status)
    return RedirectResponse(f"/admin/support-requests/{request_id}", status_code=303)
