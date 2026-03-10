from __future__ import annotations

import unittest
from decimal import Decimal

from bot.handlers.simple import _parse_amount_and_currency


class SimpleParsingTest(unittest.TestCase):
    def test_parse_amount_uses_default_currency(self) -> None:
        amount, currency = _parse_amount_and_currency("100", "ils")
        self.assertEqual(amount, Decimal("100.00"))
        self.assertEqual(currency, "ILS")

    def test_parse_amount_accepts_currency(self) -> None:
        amount, currency = _parse_amount_and_currency("12.5 usd", "ILS")
        self.assertEqual(amount, Decimal("12.50"))
        self.assertEqual(currency, "USD")

    def test_parse_amount_accepts_comma(self) -> None:
        amount, currency = _parse_amount_and_currency("12,5 EUR", "ILS")
        self.assertEqual(amount, Decimal("12.50"))
        self.assertEqual(currency, "EUR")

    def test_parse_amount_rejects_extra_tokens(self) -> None:
        with self.assertRaises(ValueError):
            _parse_amount_and_currency("100 USD lunch", "ILS")


if __name__ == "__main__":
    unittest.main()
