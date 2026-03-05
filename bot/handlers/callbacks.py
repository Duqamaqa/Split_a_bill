from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from typing import Any
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery

from ..db import Database
from ..logic import format_balance as format_balance_view
from ..models import ReminderDecision

router = Router(name="callbacks")
logger = logging.getLogger(__name__)
_TWO_DP = Decimal("0.01")


@router.callback_query(F.data == ReminderDecision.CONFIRM)
async def handle_reminder_confirm(callback: CallbackQuery) -> None:
    await callback.answer("Confirmed")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Confirmed.")


@router.callback_query(F.data == ReminderDecision.REJECT)
async def handle_reminder_reject(callback: CallbackQuery) -> None:
    await callback.answer("Rejected")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Rejected.")


@router.callback_query(F.data.startswith("tx:confirm:"))
async def handle_tx_confirm(callback: CallbackQuery, db: Database) -> None:
    await _handle_tx_action(callback=callback, db=db, action="confirm")


@router.callback_query(F.data.startswith("tx:reject:"))
async def handle_tx_reject(callback: CallbackQuery, db: Database) -> None:
    await _handle_tx_action(callback=callback, db=db, action="reject")


async def _handle_tx_action(callback: CallbackQuery, db: Database, action: str) -> None:
    """Apply confirm/reject callback to a pending transaction."""
    if callback.from_user is None:
        await callback.answer("Unable to resolve user.", show_alert=True)
        return

    tx_id = _parse_tx_callback_data(callback.data, action)
    if tx_id is None:
        await callback.answer("Invalid transaction action.", show_alert=True)
        return

    actor_profile = db.get_or_create_profile(
        telegram_user_id=callback.from_user.id,
        username=callback.from_user.username,
        display_name=_display_name(callback),
    )

    actor_profile_id = str(actor_profile.get("id", ""))

    try:
        if action == "confirm":
            updated_tx = db.confirm_transaction(tx_id=tx_id, confirmer_user_id=actor_profile_id)
        else:
            updated_tx = db.reject_transaction(tx_id=tx_id, confirmer_user_id=actor_profile_id)
    except ValueError as exc:
        message = str(exc).lower()
        if "creator cannot confirm" in message:
            await callback.answer("Only the counterparty can do this.", show_alert=True)
            return
        if "reviewer is not a member" in message:
            await callback.answer("You are not part of this transaction.", show_alert=True)
            return
        if "transaction creator is not part of friendship" in message:
            await callback.answer("Transaction data is invalid.", show_alert=True)
            return
        logger.exception("Tx callback validation failed")
        await callback.answer("Failed to update transaction.", show_alert=True)
        return
    except Exception:
        logger.exception("Failed to apply tx action")
        await callback.answer("Failed to update transaction.", show_alert=True)
        return

    if updated_tx is None:
        await callback.answer("Transaction not found.", show_alert=True)
        return

    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)

    status = str(updated_tx.get("status", "")).lower()
    if action == "confirm":
        if status != "confirmed":
            await callback.answer("Transaction is not pending anymore.", show_alert=True)
            return
        friendship = _get_friendship_by_id(db, str(updated_tx.get("friendship_id", "")))
        if friendship is None:
            await callback.answer("Friendship not found.", show_alert=True)
            return
        await callback.answer("Confirmed")
        await _notify_confirmed(db=db, callback=callback, tx=updated_tx, friendship=friendship)
        return

    if status != "rejected":
        await callback.answer("Transaction is not pending anymore.", show_alert=True)
        return

    friendship = _get_friendship_by_id(db, str(updated_tx.get("friendship_id", "")))
    if friendship is None:
        await callback.answer("Friendship not found.", show_alert=True)
        return

    await callback.answer("Rejected")
    await _notify_rejected(db=db, callback=callback, tx=updated_tx, friendship=friendship)


async def _notify_confirmed(
    db: Database,
    callback: CallbackQuery,
    tx: dict[str, Any],
    friendship: dict[str, Any],
) -> None:
    created_by = str(tx.get("created_by", ""))
    user_low = str(friendship.get("user_low", ""))
    user_high = str(friendship.get("user_high", ""))
    counterparty_id = user_high if created_by == user_low else user_low

    profiles = _get_profiles_by_ids(db, [created_by, counterparty_id])
    creator_profile = profiles.get(created_by)
    counterparty_profile = profiles.get(counterparty_id)

    amount = _to_decimal(tx.get("amount"))
    currency = str(tx.get("currency", "")).upper()
    note = str(tx.get("note", "")).strip()
    net_after = tx.get("net_amount_after")
    if net_after is None:
        balance_row = _get_balance_row(
            db=db,
            friendship_id=str(tx.get("friendship_id", "")),
            currency=currency,
        )
        net_amount = _to_decimal(balance_row.get("net_amount") if balance_row else 0)
    else:
        net_amount = _to_decimal(net_after)

    creator_text = _confirmed_message_for_viewer(
        viewer_profile_id=created_by,
        friend_profile_id=counterparty_id,
        viewer_friend_label=_profile_label(counterparty_profile),
        amount=amount,
        currency=currency,
        note=note,
        net_amount=net_amount,
    )
    counterparty_text = _confirmed_message_for_viewer(
        viewer_profile_id=counterparty_id,
        friend_profile_id=created_by,
        viewer_friend_label=_profile_label(creator_profile),
        amount=amount,
        currency=currency,
        note=note,
        net_amount=net_amount,
    )

    await _safe_send_profile_message(callback, creator_profile, creator_text)
    await _safe_send_profile_message(callback, counterparty_profile, counterparty_text)


async def _notify_rejected(
    db: Database,
    callback: CallbackQuery,
    tx: dict[str, Any],
    friendship: dict[str, Any],
) -> None:
    created_by = str(tx.get("created_by", ""))
    user_low = str(friendship.get("user_low", ""))
    user_high = str(friendship.get("user_high", ""))
    counterparty_id = user_high if created_by == user_low else user_low

    profiles = _get_profiles_by_ids(db, [created_by, counterparty_id])
    creator_profile = profiles.get(created_by)
    counterparty_profile = profiles.get(counterparty_id)

    amount = _to_decimal(tx.get("amount"))
    currency = str(tx.get("currency", "")).upper()
    note = str(tx.get("note", "")).strip()
    note_line = f"\nNote: {escape(note)}" if note else ""
    body = (
        "Transaction rejected.\n"
        f"Amount: {amount:.2f} {escape(currency)}"
        f"{note_line}\n"
        "No balance change was applied."
    )

    await _safe_send_profile_message(callback, creator_profile, body)
    await _safe_send_profile_message(callback, counterparty_profile, body)


def _confirmed_message_for_viewer(
    viewer_profile_id: str,
    friend_profile_id: str,
    viewer_friend_label: str,
    amount: Decimal,
    currency: str,
    note: str,
    net_amount: Decimal,
) -> str:
    _, abs_balance, owes_text = format_balance_view(
        viewer_id=viewer_profile_id,
        friend_id=friend_profile_id,
        net_amount=net_amount,
    )
    note_line = f"\nNote: {escape(note)}" if note else ""
    return (
        "Transaction confirmed.\n"
        f"Amount: {amount:.2f} {escape(currency)}"
        f"{note_line}\n"
        f"With {escape(viewer_friend_label)}: {abs_balance:.2f} {escape(currency)}\n"
        f"{escape(owes_text)}"
    )


def _parse_tx_callback_data(data: str | None, expected_action: str) -> str | None:
    if not data:
        return None
    parts = data.split(":", maxsplit=2)
    if len(parts) != 3:
        return None

    prefix, action, tx_id = parts
    if prefix != "tx" or action != expected_action:
        return None

    try:
        UUID(tx_id)
    except ValueError:
        return None

    return tx_id


def _get_friendship_by_id(db: Database, friendship_id: str) -> dict[str, Any] | None:
    response = db.client.table("friendships").select("*").eq("id", friendship_id).limit(1).execute()
    rows = response.data if isinstance(response.data, list) else []
    return rows[0] if rows else None


def _get_profiles_by_ids(db: Database, profile_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not profile_ids:
        return {}
    unique_ids = sorted({profile_id for profile_id in profile_ids if profile_id})
    if not unique_ids:
        return {}

    response = (
        db.client.table("profiles")
        .select("id,telegram_user_id,telegram_username,display_name")
        .in_("id", unique_ids)
        .execute()
    )
    rows = response.data if isinstance(response.data, list) else []
    return {str(row.get("id")): row for row in rows if isinstance(row, dict) and row.get("id")}


def _get_balance_row(db: Database, friendship_id: str, currency: str) -> dict[str, Any] | None:
    response = (
        db.client.table("balances")
        .select("*")
        .eq("friendship_id", friendship_id)
        .eq("currency", currency)
        .limit(1)
        .execute()
    )
    rows = response.data if isinstance(response.data, list) else []
    return rows[0] if rows else None


async def _safe_send_profile_message(
    callback: CallbackQuery,
    profile: dict[str, Any] | None,
    text: str,
) -> None:
    if not profile:
        return
    telegram_user_id = profile.get("telegram_user_id")
    if telegram_user_id is None:
        return
    try:
        await callback.bot.send_message(chat_id=int(telegram_user_id), text=text)
    except TelegramForbiddenError:
        logger.info("DM forbidden for telegram_user_id=%s", telegram_user_id)
    except Exception:
        logger.info("Failed to send tx status message to telegram_user_id=%s", telegram_user_id, exc_info=True)


def _profile_label(profile: dict[str, Any] | None) -> str:
    if not profile:
        return "friend"

    display_name = str(profile.get("display_name", "")).strip()
    if display_name:
        return display_name

    username = str(profile.get("telegram_username", "")).strip()
    if username:
        return f"@{username}"

    telegram_user_id = profile.get("telegram_user_id")
    return f"user {telegram_user_id}" if telegram_user_id else "friend"


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _display_name(callback: CallbackQuery) -> str | None:
    first_name = (callback.from_user.first_name or "").strip()
    last_name = (callback.from_user.last_name or "").strip()
    full = f"{first_name} {last_name}".strip()
    return full or None
