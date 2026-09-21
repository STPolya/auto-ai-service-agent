"""Display-only formatting; workflow decisions remain in domain definitions."""

from datetime import timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.services.booking_rules import BOOKING_TIMEZONE
from app.services.support_status import NEW, IN_PROGRESS, RESOLVED, CANCELLED, ALLOWED_STATUSES, STATUS_TRANSITIONS

WEB_DIRECTORY = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIRECTORY / "templates"))
STATUS_LABELS = {NEW: "Новые", IN_PROGRESS: "В работе", RESOLVED: "Решены", CANCELLED: "Отменены"}
STATUS_NAMES = {NEW: "Новое", IN_PROGRESS: "В работе", RESOLVED: "Решено", CANCELLED: "Отменено"}
ACTION_LABELS = {IN_PROGRESS: "Взять в работу", RESOLVED: "Отметить решённым", CANCELLED: "Отменить обращение"}


def date_label(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BOOKING_TIMEZONE).strftime("%d.%m.%Y · %H:%M")


def customer_label(user):
    return user["first_name"] or ("@" + user["username"] if user["username"] else f"Клиент {user['telegram_id']}")


def available_actions(status):
    return [(target, ACTION_LABELS[target]) for target in ALLOWED_STATUSES if target in STATUS_TRANSITIONS[status]]


templates.env.filters.update(date_label=date_label, customer_label=customer_label)
templates.env.globals.update(status_labels=STATUS_LABELS, status_names=STATUS_NAMES, available_actions=available_actions)


def render(request, template, *, session=None, status_code=200, **context):
    return templates.TemplateResponse(request=request, name=template, status_code=status_code,
                                      context={"session": session, **context},
                                      headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                               "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
                                               "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})


def is_web_path(path: str) -> bool:
    return path == "/admin" or path.startswith("/admin/")


def error_page(request, code, message):
    titles = {403: "Форма устарела", 404: "Обращение не найдено", 409: "Статус уже изменился",
              422: "Проверьте введённые данные", 500: "Не удалось загрузить страницу", 503: "Сервис временно недоступен"}
    return render(request, "error.html", status_code=code, code=code,
                  title=titles.get(code, "Не удалось выполнить запрос"), message=message)
