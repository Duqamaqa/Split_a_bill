from __future__ import annotations

import unittest
from decimal import Decimal

from bot.logic import (
    NET_SIGN_CONVENTION,
    apply_currency_delta,
    canonical_pair,
    compute_delta,
    format_balance,
    parse_entry_args,
)


USER_LOW = "00000000-0000-0000-0000-000000000001"
USER_HIGH = "00000000-0000-0000-0000-0000000000ff"


class LogicBalanceRulesTest(unittest.TestCase):
    def test_delta_creator_low_out_increases_net(self) -> None:
        delta = compute_delta(
            NET_SIGN_CONVENTION,
            USER_LOW,
            USER_HIGH,
            USER_LOW,
            "out",
            Decimal("10.00"),
        )
        self.assertEqual(delta, Decimal("10.00"))

    def test_delta_creator_low_in_decreases_net(self) -> None:
        delta = compute_delta(
            NET_SIGN_CONVENTION,
            USER_LOW,
            USER_HIGH,
            USER_LOW,
            "in",
            Decimal("10.00"),
        )
        self.assertEqual(delta, Decimal("-10.00"))

    def test_delta_creator_high_out_decreases_net(self) -> None:
        delta = compute_delta(
            NET_SIGN_CONVENTION,
            USER_LOW,
            USER_HIGH,
            USER_HIGH,
            "out",
            Decimal("10.00"),
        )
        self.assertEqual(delta, Decimal("-10.00"))

    def test_delta_creator_high_in_increases_net(self) -> None:
        delta = compute_delta(
            NET_SIGN_CONVENTION,
            USER_LOW,
            USER_HIGH,
            USER_HIGH,
            "in",
            Decimal("10.00"),
        )
        self.assertEqual(delta, Decimal("10.00"))

    def test_format_positive_net_viewer_low_is_owed(self) -> None:
        low, high = canonical_pair(USER_HIGH, USER_LOW)
        self.assertEqual((low, high), (USER_LOW, USER_HIGH))

        label, abs_amount, text = format_balance(USER_LOW, USER_HIGH, Decimal("7.50"))
        self.assertEqual(label, "you_are_owed")
        self.assertEqual(abs_amount, Decimal("7.50"))
        self.assertEqual(text, "Friend owes you.")

    def test_format_positive_net_viewer_high_owes(self) -> None:
        label, abs_amount, text = format_balance(USER_HIGH, USER_LOW, Decimal("7.50"))
        self.assertEqual(label, "you_owe")
        self.assertEqual(abs_amount, Decimal("7.50"))
        self.assertEqual(text, "You owe your friend.")

    def test_format_negative_net_viewer_low_owes(self) -> None:
        label, abs_amount, text = format_balance(USER_LOW, USER_HIGH, Decimal("-3.25"))
        self.assertEqual(label, "you_owe")
        self.assertEqual(abs_amount, Decimal("3.25"))
        self.assertEqual(text, "You owe your friend.")

    def test_format_zero_net_is_settled(self) -> None:
        label, abs_amount, text = format_balance(USER_HIGH, USER_LOW, Decimal("0.00"))
        self.assertEqual(label, "settled")
        self.assertEqual(abs_amount, Decimal("0.00"))
        self.assertEqual(text, "All settled.")


class LogicCurrencyParsingTest(unittest.TestCase):
    def test_parse_entry_uses_default_currency_when_missing(self) -> None:
        amount, currency, note = parse_entry_args("100", "ils")
        self.assertEqual(amount, Decimal("100.00"))
        self.assertEqual(currency, "ILS")
        self.assertEqual(note, "")

    def test_parse_entry_supports_lowercase_rub(self) -> None:
        amount, currency, note = parse_entry_args("100 rub groceries", "ILS")
        self.assertEqual(amount, Decimal("100.00"))
        self.assertEqual(currency, "RUB")
        self.assertEqual(note, "groceries")

    def test_parse_entry_supports_compact_currency(self) -> None:
        amount, currency, note = parse_entry_args("12.5usd lunch", "EUR")
        self.assertEqual(amount, Decimal("12.50"))
        self.assertEqual(currency, "USD")
        self.assertEqual(note, "lunch")

    def test_parse_entry_rejects_unsupported_currency(self) -> None:
        with self.assertRaises(ValueError):
            parse_entry_args("100 gbp", "ILS")

    def test_apply_currency_delta_keeps_other_currencies_unchanged(self) -> None:
        updated = apply_currency_delta(
            {"ILS": Decimal("10.00"), "USD": Decimal("-3.00")},
            "RUB",
            Decimal("5.00"),
        )
        self.assertEqual(updated["ILS"], Decimal("10.00"))
        self.assertEqual(updated["USD"], Decimal("-3.00"))
        self.assertEqual(updated["RUB"], Decimal("5.00"))


if __name__ == "__main__":
    unittest.main()
