from __future__ import annotations

import unittest
from decimal import Decimal

from bot.handlers.simple import _balance_summary_for_button, _parse_amount_and_currency


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

    def test_balance_summary_uses_plus_when_you_owe(self) -> None:
        summary = _balance_summary_for_button(
            [
                {
                    "currency": "USD",
                    "they_owe_you": "0",
                    "you_owe": "15.00",
                }
            ]
        )
        self.assertEqual(summary, "+15 USD")

    def test_balance_summary_uses_minus_when_friend_owes_you(self) -> None:
        summary = _balance_summary_for_button(
            [
                {
                    "currency": "ILS",
                    "they_owe_you": "20.50",
                    "you_owe": "0",
                }
            ]
        )
        self.assertEqual(summary, "-20.5 ILS")


if __name__ == "__main__":
    unittest.main()
