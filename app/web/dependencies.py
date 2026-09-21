"""Signed opaque browser sessions and synchronizer CSRF tokens.

Single-process portfolio session store. Restart logs users out; no raw admin key
or conversation data is placed in the cookie. Logout revokes replayed cookies too.
"""

from dataclasses import dataclass
import hashlib
import secrets
from threading import Lock
import time

from fastapi import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config.settings import get_admin_api_key, get_web_cookie_secure, get_web_session_secret
from app.security import keys_match

COOKIE_NAME = "autocare_crm"
SESSION_SECONDS = 8 * 60 * 60
LOGIN_SECONDS = 10 * 60
MAX_SESSIONS = 1024


class WebError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


class LoginRequired(Exception):
    pass


@dataclass(frozen=True)
class BrowserSession:
    id: str
    csrf: str
    expires_at: float
    key_fingerprint: str | None = None


_sessions: dict[str, BrowserSession] = {}
_lock = Lock()


def signer() -> URLSafeTimedSerializer:
    try:
        return URLSafeTimedSerializer(get_web_session_secret(), salt="autocare-crm-session")
    except ValueError:
        raise WebError(503, "Вход временно недоступен. Обратитесь к администратору.") from None


def _prune() -> None:
    for key in [key for key, value in _sessions.items() if value.expires_at <= time.time()]:
        del _sessions[key]


def new_session(key: str | None = None) -> BrowserSession:
    session = BrowserSession(secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                             time.time() + (SESSION_SECONDS if key else LOGIN_SECONDS),
                             hashlib.sha256(key.encode()).hexdigest() if key else None)
    with _lock:
        _prune()
        if len(_sessions) >= MAX_SESSIONS:
            # Anonymous login-page traffic must never evict an operator.
            anonymous = next((sid for sid, value in _sessions.items()
                              if value.key_fingerprint is None), None)
            if anonymous is None:
                raise WebError(503, "Вход временно недоступен. Попробуйте позже.")
            del _sessions[anonymous]
        _sessions[session.id] = session
    return session


def revoke(session: BrowserSession) -> None:
    with _lock:
        _sessions.pop(session.id, None)


def set_cookie(response, session: BrowserSession) -> None:
    try:
        secure = get_web_cookie_secure()
    except ValueError:
        raise WebError(503, "Вход временно недоступен. Обратитесь к администратору.") from None
    response.set_cookie(COOKIE_NAME, signer().dumps(session.id),
                        max_age=SESSION_SECONDS if session.key_fingerprint else LOGIN_SECONDS,
                        httponly=True, secure=secure, samesite="lax", path="/admin")


def read_session(request: Request) -> BrowserSession | None:
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return None
    try:
        session_id = signer().loads(cookie, max_age=SESSION_SECONDS)
    except BadSignature:
        return None
    if not isinstance(session_id, str):
        return None
    with _lock:
        _prune()
        return _sessions.get(session_id)


def require_session(request: Request) -> BrowserSession:
    session = read_session(request)
    if session is None or session.key_fingerprint is None:
        raise LoginRequired
    try:
        fingerprint = hashlib.sha256(get_admin_api_key().encode()).hexdigest()
    except ValueError:
        revoke(session)
        raise LoginRequired from None
    if not keys_match(session.key_fingerprint, fingerprint):
        revoke(session)
        raise LoginRequired
    return session


def check_csrf(session: BrowserSession | None, token: str) -> None:
    if session is None or not token or not keys_match(token, session.csrf):
        raise WebError(403, "Не удалось проверить форму. Обновите страницу и попробуйте ещё раз.")
