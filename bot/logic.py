from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Mapping
from uuid import UUID

from .currency import normalize_currency_code
from .models import BalanceSnapshot, Direction, LedgerEntry

_AMOUNT_RE = re.compile(r"^([+-]?[0-9][0-9\.,]*)([A-Za-z]{3})?$")
_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")
_TWO_DP = Decimal("0.01")
_ZERO = Decimal("0.00")
NET_SIGN_CONVENTION = "user_high_owes_user_low_positive"


def parse_entry_args(raw_args: str | None, default_currency: str) -> tuple[Decimal, str, str]:
    """
    Parse command args for `/in` and `/out`.

    Supported formats:
    - `12.5`
    - `12.5 USD`
    - `12.5USD`
    - `12.5 lunch with team`
    - `12.5 USD lunch with team`
    """
    if not raw_args or not raw_args.strip():
        raise ValueError("Missing amount.")

    tokens = raw_args.strip().split()
    amount_token = tokens[0]
    amount, compact_currency = _parse_amount_token(amount_token)

    currency = normalize_currency_code(compact_currency, fallback=default_currency)
    note_start = 1

    if len(tokens) > 1 and _CURRENCY_RE.fullmatch(tokens[1]):
        currency = normalize_currency_code(tokens[1])
        note_start = 2

    note = " ".join(tokens[note_start:]).strip()
    return amount, currency, note


def compute_snapshot(entries: Iterable[LedgerEntry], currency: str) -> BalanceSnapshot:
    total_in = Decimal("0")
    total_out = Decimal("0")

    for entry in entries:
        if entry.direction == "in":
            total_in += entry.amount
        else:
            total_out += entry.amount

    return BalanceSnapshot(currency=currency.upper(), total_in=total_in, total_out=total_out)


def compute_balance(entries: Iterable[LedgerEntry]) -> Decimal:
    balance = Decimal("0")
    for entry in entries:
        balance = apply_entry(balance, entry.amount, entry.direction)
    return balance


def apply_entry(balance: Decimal, amount: Decimal, direction: Direction) -> Decimal:
    if direction == "in":
        return balance + amount
    return balance - amount


def canonical_pair(a_uuid: str, b_uuid: str) -> tuple[str, str]:
    left = _normalize_uuid(a_uuid)
    right = _normalize_uuid(b_uuid)
    if left <= right:
        return left, right
    return right, left


def compute_delta(
    net_sign_convention: str,
    user_low: str,
    user_high: str,
    creator_id: str,
    direction: Direction | str,
    amount: Decimal | str | int | float,
) -> Decimal:
    """
    Compute change to balances.net_amount.

    Convention:
      net_amount > 0 means user_high owes user_low.
      net_amount < 0 means user_low owes user_high.
    """
    if net_sign_convention != NET_SIGN_CONVENTION:
        raise ValueError(f"Unsupported sign convention: {net_sign_convention}")

    normalized_low = _normalize_uuid(user_low)
    normalized_high = _normalize_uuid(user_high)
    if (normalized_low, normalized_high) != canonical_pair(normalized_low, normalized_high):
        raise ValueError("Friendship pair must be canonical (user_low, user_high)")

    normalized_creator = _normalize_uuid(creator_id)
    if normalized_creator not in {normalized_low, normalized_high}:
        raise ValueError("creator_id must match user_low or user_high")

    normalized_direction = str(direction).strip().lower()
    if normalized_direction not in {"in", "out"}:
        raise ValueError("direction must be 'in' or 'out'")

    normalized_amount = _normalize_amount(amount)

    if normalized_creator == normalized_low:
        return normalized_amount if normalized_direction == "out" else -normalized_amount

    return -normalized_amount if normalized_direction == "out" else normalized_amount


def format_balance(
    viewer_id: str,
    friend_id: str,
    net_amount: Decimal | str | int | float,
) -> tuple[str, Decimal, str]:
    """
    Return (label, abs_amount, human_text) from viewer perspective.
    """
    viewer = _normalize_uuid(viewer_id)
    friend = _normalize_uuid(friend_id)
    if viewer == friend:
        raise ValueError("viewer_id and friend_id must be different")

    user_low, user_high = canonical_pair(viewer, friend)
    normalized_net = _normalize_net_amount(net_amount)
    abs_amount = abs(normalized_net).quantize(_TWO_DP, rounding=ROUND_HALF_UP)

    if normalized_net == _ZERO:
        return "settled", abs_amount, "All settled."

    if normalized_net > _ZERO:
        debtor, creditor = user_high, user_low
    else:
        debtor, creditor = user_low, user_high

    if viewer == creditor:
        return "you_are_owed", abs_amount, "Friend owes you."

    if viewer == debtor:
        return "you_owe", abs_amount, "You owe your friend."

    raise ValueError("viewer_id must belong to the friendship pair")


def apply_currency_delta(
    balances_by_currency: Mapping[str, Decimal | str | int | float],
    currency: str,
    delta: Decimal | str | int | float,
) -> dict[str, Decimal]:
    """
    Apply a net delta to one currency while keeping other currencies unchanged.
    """
    updated: dict[str, Decimal] = {}
    for raw_currency, raw_amount in balances_by_currency.items():
        updated[normalize_currency_code(str(raw_currency))] = _normalize_net_amount(raw_amount)

    normalized_currency = normalize_currency_code(currency)
    normalized_delta = _normalize_net_amount(delta)
    current = updated.get(normalized_currency, _ZERO)
    updated[normalized_currency] = (current + normalized_delta).quantize(
        _TWO_DP,
        rounding=ROUND_HALF_UP,
    )
    return updated


def _parse_amount_token(token: str) -> tuple[Decimal, str | None]:
    match = _AMOUNT_RE.fullmatch(token)
    if not match:
        raise ValueError("Invalid amount format.")

    amount_part, currency_part = match.groups()

    amount = _parse_decimal_amount(amount_part)

    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    currency = currency_part.upper() if currency_part else None
    return amount, currency


def _normalize_amount(value: Decimal | str | int | float) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Invalid amount value.") from exc

    if amount <= _ZERO:
        raise ValueError("Amount must be greater than zero.")
    return amount


def _parse_decimal_amount(raw_amount: str) -> Decimal:
    cleaned = raw_amount.strip().replace(" ", "")
    if not cleaned:
        raise ValueError("Invalid amount value.")

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            normalized = cleaned.replace(".", "").replace(",", ".")
        else:
            normalized = cleaned.replace(",", "")
    elif "," in cleaned:
        if cleaned.count(",") == 1 and len(cleaned.split(",", maxsplit=1)[1]) <= 2:
            normalized = cleaned.replace(",", ".")
        else:
            normalized = cleaned.replace(",", "")
    else:
        normalized = cleaned

    try:
        return Decimal(normalized).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Invalid amount value.") from exc


def _normalize_net_amount(value: Decimal | str | int | float) -> Decimal:
    try:
        return Decimal(str(value)).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Invalid net_amount value.") from exc


def _normalize_uuid(value: str) -> str:
    return str(UUID(str(value)))
