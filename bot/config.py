from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .currency import normalize_currency_code


class Settings(BaseSettings):
    BOT_TOKEN: SecretStr = Field(..., min_length=10)
    BOT_USERNAME: str | None = Field(default=None)
    DATABASE_URL: SecretStr | None = Field(default=None, min_length=1)
    POSTGRES_URL: SecretStr | None = Field(default=None, min_length=1)
    DEFAULT_CURRENCY: str = Field(default="ILS", min_length=3, max_length=3)
    WEBHOOK_SECRET: SecretStr | None = Field(default=None, min_length=1)
    PUBLIC_BASE_URL: str | None = Field(default=None)

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("DEFAULT_CURRENCY")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        return normalize_currency_code(value)

    @field_validator("BOT_USERNAME")
    @classmethod
    def _normalize_bot_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lstrip("@")
        return normalized or None

    @field_validator("PUBLIC_BASE_URL")
    @classmethod
    def _normalize_public_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip().rstrip("/")
        if not normalized:
            return None

        if not normalized.startswith(("http://", "https://")):
            raise ValueError("PUBLIC_BASE_URL must start with http:// or https://")

        return normalized

    @property
    def bot_token(self) -> str:
        return self.BOT_TOKEN.get_secret_value()

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL is not None:
            return self.DATABASE_URL.get_secret_value()
        if self.POSTGRES_URL is not None:
            return self.POSTGRES_URL.get_secret_value()
        raise RuntimeError("DATABASE_URL or POSTGRES_URL is required")

    @property
    def webhook_secret(self) -> str | None:
        if self.WEBHOOK_SECRET is None:
            return None
        return self.WEBHOOK_SECRET.get_secret_value()

    @property
    def public_base_url(self) -> str | None:
        return self.PUBLIC_BASE_URL

    @property
    def telegram_webhook_url(self) -> str:
        if not self.PUBLIC_BASE_URL:
            raise RuntimeError("PUBLIC_BASE_URL is required to build the Telegram webhook URL")
        return f"{self.PUBLIC_BASE_URL}/api/telegram"


def _worker_settings_overrides() -> dict[str, Any]:
    try:
        from workers import env as worker_env  # type: ignore[import-not-found]
    except Exception:
        return {}

    overrides: dict[str, Any] = {}
    for field_name in Settings.model_fields:
        value = getattr(worker_env, field_name, None)
        if value is not None:
            overrides[field_name] = value

    return overrides


@lru_cache(maxsize=1)
def _get_local_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    worker_overrides = _worker_settings_overrides()
    if worker_overrides:
        return Settings(**worker_overrides)
    return _get_local_settings()
