from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .currency import normalize_currency_code


class Settings(BaseSettings):
    BOT_TOKEN: SecretStr = Field(..., min_length=10)
    BOT_USERNAME: str | None = Field(default=None)
    DATABASE_URL: SecretStr = Field(..., min_length=1)
    DEFAULT_CURRENCY: str = Field(default="ILS", min_length=3, max_length=3)

    model_config = SettingsConfigDict(
        env_file=".env",
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

    @property
    def bot_token(self) -> str:
        return self.BOT_TOKEN.get_secret_value()

    @property
    def database_url(self) -> str:
        return self.DATABASE_URL.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
