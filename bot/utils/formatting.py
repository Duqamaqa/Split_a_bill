from __future__ import annotations

from datetime import timezone
from decimal import Decimal
from html import escape
from typing import Mapping

from ..models import LedgerEntry


def format_money(amount: Decimal, currency: str) -> str:
    return f"{amount:,.2f} {currency.upper()}"


def format_balance(amount: Decimal, currency: str) -> str:
    return f"Current balance: <b>{format_money(amount, currency)}</b>"


def format_history(entries: list[LedgerEntry]) -> str:
    if not entries:
        return "No entries yet."

    lines = ["<b>Latest transactions</b>"]
    for entry in entries:
        sign = "+" if entry.direction == "in" else "-"
        timestamp = "unknown"
        if entry.created_at is not None:
            timestamp = (
                entry.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                if entry.created_at.tzinfo
                else entry.created_at.strftime("%Y-%m-%d %H:%M")
            )

        note_suffix = f" | {entry.note}" if entry.note else ""
        lines.append(
            f"{timestamp} | {sign}{format_money(entry.amount, entry.currency)}{note_suffix}"
        )

    return "\n".join(lines)


def format_retry_after(seconds: int) -> str:
    minutes, sec = divmod(max(seconds, 0), 60)
    hours, min_ = divmod(minutes, 60)

    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if min_:
        parts.append(f"{min_}m")
    if sec or not parts:
        parts.append(f"{sec}s")

    return " ".join(parts)


def format_generic_balance_line(
    *,
    currency: str,
    they_owe_you: Decimal,
    you_owe: Decimal,
) -> str:
    normalized_currency = currency.upper()
    if they_owe_you > Decimal("0"):
        return f"🟢 Friend owes you {format_money(they_owe_you, normalized_currency)}"
    if you_owe > Decimal("0"):
        return f"🔴 You owe friend {format_money(you_owe, normalized_currency)}"
    return "⚪ Settled"


def format_named_balance_line(
    *,
    friend_label: str,
    currency: str,
    they_owe_you: Decimal,
    you_owe: Decimal,
) -> str:
    normalized_currency = currency.upper()
    safe_label = escape(friend_label)
    if they_owe_you > Decimal("0"):
        return f"🟢 {safe_label} owes you {format_money(they_owe_you, normalized_currency)}"
    if you_owe > Decimal("0"):
        return f"🔴 You owe {safe_label} {format_money(you_owe, normalized_currency)}"
    return "⚪ Settled"


def short_tx_suffix(tx_id: str, length: int = 8) -> str:
    cleaned = tx_id.strip()
    if not cleaned:
        return "--------"
    return cleaned[-length:]


def format_transaction_history_line(
    *,
    tx: Mapping[str, object],
    creator_label: str,
) -> str:
    tx_id = str(tx.get("id", ""))
    status = str(tx.get("status", "pending")).upper()
    direction = str(tx.get("direction", "out")).lower()
    try:
        amount = Decimal(str(tx.get("amount", "0")))
    except Exception:
        amount = Decimal("0")
    currency = str(tx.get("currency", "ILS")).upper()
    note = str(tx.get("note", "")).strip()

    creator_text = escape(creator_label)
    suffix = short_tx_suffix(tx_id)
    direction_text = "OUT" if direction == "out" else "IN"
    note_suffix = f" | {escape(note)}" if note else ""
    return (
        f"`{suffix}` | {status} | {creator_text} | "
        f"{direction_text} {format_money(amount, currency)}{note_suffix}"
    )
