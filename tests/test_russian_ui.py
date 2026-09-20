"""Russian presentation contracts without network or database access."""

import unittest

from app.bot.handlers.diagnostics import FOLLOWUP_MESSAGE, PROMPT_MESSAGE
from app.bot.handlers.services import format_service
from app.bot.keyboards.main_menu import main_menu_keyboard
from app.bot.keyboards.vehicles import add_vehicle_keyboard
from app.bot.presentation import service_name, service_description, status_label
from app.database.models import Service
from scripts.seed_services import INITIAL_SERVICES


class RussianUITests(unittest.TestCase):
    def test_exact_russian_main_menu(self):
        self.assertEqual([button.text for row in main_menu_keyboard().keyboard for button in row], [
            "🤖 Описать проблему", "📅 Записаться на сервис", "🔧 Услуги и цены",
            "📋 Мои записи", "🚗 Мои автомобили",
        ])
        self.assertEqual(add_vehicle_keyboard().inline_keyboard[0][0].text, "➕ Добавить автомобиль")

    def test_seed_services_translate_for_display_without_mutation(self):
        names = ["Замена масла", "Диагностика тормозной системы", "Компьютерная диагностика",
                 "Замена шин", "Обслуживание кондиционера"]
        for row, russian_name in zip(INITIAL_SERVICES, names):
            name, description, price, duration = row
            service = Service(name=name, description=description, price_from=price, duration_minutes=duration)
            text = format_service(service)
            self.assertIn(russian_name, text)
            self.assertNotIn(name, text)
            self.assertNotIn(description, text)
            self.assertEqual((service.name, service.description), (name, description))
            self.assertEqual(service_name(name), russian_name)
            self.assertTrue(any("а" <= char.lower() <= "я" for char in service_description(description)))

    def test_custom_russian_content_preserved(self):
        self.assertEqual(service_name("Мойка автомобиля"), "Мойка автомобиля")
        self.assertEqual(service_description("Ручная мойка."), "Ручная мойка.")
        self.assertIsNone(service_description(None))

    def test_internal_status_never_exposed(self):
        self.assertEqual(status_label("scheduled"), "Ожидает подтверждения менеджером")
        self.assertEqual(status_label("unexpected_internal_value"), "Уточняется")

    def test_russian_diagnostic_prompt_and_cta(self):
        self.assertTrue(PROMPT_MESSAGE.startswith("Опишите, что происходит с автомобилем:"))
        self.assertEqual(FOLLOWUP_MESSAGE, "Если хотите записаться на диагностику, выберите «📅 Записаться на сервис».")
