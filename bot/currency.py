from __future__ import annotations

from typing import Iterable

SUPPORTED_CURRENCIES: tuple[str, ...] = ("ILS", "USD", "EUR", "RUB")
SUPPORTED_CURRENCY_SET = frozenset(SUPPORTED_CURRENCIES)


def normalize_currency_code(value: str | None, *, fallback: str | None = None) -> str:
    """Normalize and validate a supported currency code."""
    candidate = value if value is not None else fallback
    if candidate is None:
        raise ValueError("Currency is required.")

    normalized = candidate.strip().upper()
    if normalized not in SUPPORTED_CURRENCY_SET:
        raise ValueError(
            f"Unsupported currency '{normalized}'. "
            f"Supported: {', '.join(SUPPORTED_CURRENCIES)}."
        )
    return normalized


def is_currency_token(token: str) -> bool:
    """Return True when the token looks like a currency code candidate."""
    cleaned = token.strip()
    return len(cleaned) == 3 and cleaned.isalpha()


def currencies_text(sep: str = "/") -> str:
    return sep.join(SUPPORTED_CURRENCIES)


def normalize_currency_iter(values: Iterable[str]) -> list[str]:
    return [normalize_currency_code(value) for value in values]
