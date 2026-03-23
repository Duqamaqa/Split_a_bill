from __future__ import annotations

import unittest

from bot.config import Settings
from bot.webhook_app import is_valid_telegram_secret


def make_settings(**overrides: object) -> Settings:
    values = {
        "BOT_TOKEN": "1234567890:TESTTOKEN",
        "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/split_bill",
        "DEFAULT_CURRENCY": "ILS",
        "WEBHOOK_SECRET": None,
        "PUBLIC_BASE_URL": None,
    }
    values.update(overrides)
    return Settings(**values)


class WebhookConfigTest(unittest.TestCase):
    def test_public_base_url_is_normalized(self) -> None:
        settings = make_settings(PUBLIC_BASE_URL="https://example.com/")
        self.assertEqual(settings.public_base_url, "https://example.com")
        self.assertEqual(settings.telegram_webhook_url, "https://example.com/api/telegram")

    def test_database_url_falls_back_to_postgres_url(self) -> None:
        settings = make_settings(
            DATABASE_URL=None,
            POSTGRES_URL="postgresql://postgres:postgres@localhost:5432/fallback_db",
        )
        self.assertEqual(
            settings.database_url,
            "postgresql://postgres:postgres@localhost:5432/fallback_db",
        )

    def test_webhook_secret_is_optional(self) -> None:
        settings = make_settings()
        self.assertTrue(is_valid_telegram_secret(settings, None))
        self.assertTrue(is_valid_telegram_secret(settings, "anything"))

    def test_webhook_secret_must_match_header(self) -> None:
        settings = make_settings(WEBHOOK_SECRET="super-secret")
        self.assertTrue(is_valid_telegram_secret(settings, "super-secret"))
        self.assertFalse(is_valid_telegram_secret(settings, None))
        self.assertFalse(is_valid_telegram_secret(settings, "wrong-secret"))


if __name__ == "__main__":
    unittest.main()
