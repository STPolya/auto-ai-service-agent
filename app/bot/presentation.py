"""Russian presentation of known seed content; never mutate database rows."""

SERVICE_NAMES = {
    "Oil change": "Замена масла",
    "Brake inspection": "Диагностика тормозной системы",
    "Computer diagnostics": "Компьютерная диагностика",
    "Tire change": "Замена шин",
    "Air conditioning service": "Обслуживание кондиционера",
}
SERVICE_DESCRIPTIONS = {
    "Engine oil and oil filter replacement.": "Замена моторного масла и масляного фильтра.",
    "Inspection of brake pads, discs, and braking system.": "Проверка тормозных колодок, дисков и работы тормозной системы.",
    "Electronic diagnostics and fault-code scan.": "Компьютерная диагностика электронных систем и считывание кодов ошибок.",
    "Seasonal tire replacement for one vehicle.": "Сезонная замена шин на одном автомобиле.",
    "Air-conditioning system inspection and service.": "Проверка и обслуживание системы кондиционирования автомобиля.",
}
STATUS_LABELS = {
    "scheduled": "Ожидает подтверждения менеджером",
    "confirmed": "Подтверждена",
    "cancelled": "Отменена",
    "completed": "Завершена",
}


def service_name(name: str) -> str:
    return SERVICE_NAMES.get(name, name)


def service_description(description: str | None) -> str | None:
    return SERVICE_DESCRIPTIONS.get(description, description)


def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, "Уточняется")
