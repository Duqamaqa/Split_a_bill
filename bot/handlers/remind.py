from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from typing import Any
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..config import Settings
from ..currency import normalize_currency_code
from ..db import Database
from ..logic import canonical_pair
from ..utils.formatting import format_money, format_retry_after

router = Router(name="remind")
logger = logging.getLogger(__name__)
_TWO_DP = Decimal("0.01")
_REMIND_PICK_PREFIX = "rmpick"


@router.message(Command("remind"))
async def handle_remind(
    message: Message,
    command: CommandObject,
    settings: Settings,
    db: Database,
) -> None:
    """Send debt reminders with per-friendship-per-currency cooldown enforcement."""
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    if not (command.args or "").strip():
        await _start_remind_friend_picker(message=message, db=db)
        return

    try:
        parsed = _parse_remind_args(command.args)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    if parsed is None:
        await message.answer(
            "Usage: /remind @friend [currency]\n"
            "Examples:\n"
            "/remind @friend\n"
            "/remind @friend USD"
        )
        return

    target_username, requested_currency = parsed

    requester_profile = db.get_or_create_profile(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        display_name=_display_name(message),
    )

    target_profile = db.get_profile_by_username(target_username)
    if target_profile is None:
        await message.answer(f"I couldn't find @{target_username}. Ask them to run /start first.")
        return

    await _send_reminders(
        message=message,
        settings=settings,
        db=db,
        requester_profile=requester_profile,
        target_profile=target_profile,
        requested_currency=requested_currency,
    )


@router.callback_query(F.data.startswith(f"{_REMIND_PICK_PREFIX}:"))
async def handle_remind_friend_pick(
    callback: CallbackQuery,
    settings: Settings,
    db: Database,
) -> None:
    if callback.from_user is None:
        await callback.answer("Unable to resolve user.", show_alert=True)
        return

    if callback.message is None:
        await callback.answer("Message context is missing.", show_alert=True)
        return

    target_profile_id = _parse_pick_callback(callback.data, _REMIND_PICK_PREFIX)
    if target_profile_id is None:
        await callback.answer("Invalid selection.", show_alert=True)
        return

    requester_profile = db.get_or_create_profile(
        telegram_user_id=callback.from_user.id,
        username=callback.from_user.username,
        display_name=_display_name_from_callback(callback),
    )
    target_profile = _get_profile_by_id(db=db, profile_id=target_profile_id)
    if target_profile is None:
        await callback.answer("Friend not found.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await _send_reminders(
        message=callback.message,
        settings=settings,
        db=db,
        requester_profile=requester_profile,
        target_profile=target_profile,
        requested_currency=None,
    )
    await callback.answer("Done")


async def _send_reminders(
    *,
    message: Message,
    settings: Settings,
    db: Database,
    requester_profile: dict[str, Any],
    target_profile: dict[str, Any],
    requested_currency: str | None,
) -> None:
    requester_id = str(requester_profile.get("id", ""))
    target_id = str(target_profile.get("id", ""))
    target_username = str(target_profile.get("telegram_username", "")).strip() or "friend"

    if not requester_id or not target_id:
        await message.answer("Profile data is incomplete. Try again.")
        return

    if requester_id == target_id:
        await message.answer("You cannot send a reminder to yourself.")
        return

    friendship = _get_accepted_friendship(db=db, left_id=requester_id, right_id=target_id)
    if friendship is None:
        friendship_any = _get_friendship_between(db=db, left_id=requester_id, right_id=target_id)
        await message.answer(_friendship_error_text(target_username=target_username, friendship=friendship_any))
        return

    friendship_id = str(friendship.get("id", ""))
    if not friendship_id:
        await message.answer("Friendship data is invalid.")
        return

    balance_rows = _get_balance_rows(
        db=db,
        friendship_id=friendship_id,
        currency=requested_currency,
    )
    non_zero_rows = [
        row for row in balance_rows if _to_decimal(row.get("net_amount")) != Decimal("0.00")
    ]
    pending_rows = _get_pending_confirmation_rows(
        db=db,
        friendship_id=friendship_id,
        created_by=requester_id,
        currency=requested_currency,
    )

    if not non_zero_rows and not pending_rows:
        await message.answer("You’re settled with this friend. No reminder sent.")
        return

    user_low = str(friendship.get("user_low", ""))
    user_high = str(friendship.get("user_high", ""))
    if not user_low or not user_high:
        await message.answer("Friendship participants are invalid.")
        return

    requester_username = str(requester_profile.get("telegram_username", "")).strip()
    requester_mention = f"@{requester_username}" if requester_username else "requester"

    target_tg_id = target_profile.get("telegram_user_id")
    if target_tg_id is None:
        await message.answer("User has no Telegram ID saved; reminder was not sent.")
        return

    sent_currencies: list[str] = []
    cooldowns: list[tuple[str, int]] = []
    pending_sent = False

    for row in sorted(non_zero_rows, key=lambda item: str(item.get("currency", ""))):
        currency = str(row.get("currency", "ILS")).upper()
        allowed, retry_after = _is_reminder_allowed(
            db=db,
            friendship_id=friendship_id,
            currency=currency,
            cooldown_seconds=settings.REMIND_COOLDOWN_SECONDS,
        )
        if not allowed:
            cooldowns.append((currency, retry_after))
            continue

        net_amount = _to_decimal(row.get("net_amount"))
        they_owe_requester = _they_owe_requester(
            requester_id=requester_id,
            user_low=user_low,
            user_high=user_high,
            net_amount=net_amount,
        )
        amount_abs = abs(net_amount).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
        if they_owe_requester:
            dm_text = (
                f"Reminder: you owe {escape(requester_mention)} "
                f"{format_money(amount_abs, currency)}"
            )
        else:
            dm_text = (
                f"FYI: {escape(requester_mention)} owes you "
                f"{format_money(amount_abs, currency)}"
            )

        try:
            await message.bot.send_message(chat_id=int(target_tg_id), text=dm_text)
        except TelegramForbiddenError:
            logger.info("Reminder DM forbidden for telegram_user_id=%s", target_tg_id)
            await message.answer(
                "I can't DM this user (they blocked the bot or never started it). "
                "No reminder was sent."
            )
            return
        except Exception:
            logger.info("Failed to DM reminder to telegram_user_id=%s", target_tg_id, exc_info=True)
            await message.answer(
                "I couldn't send the reminder due to Telegram DM restrictions. "
                "Ask them to open the bot and allow messages."
            )
            return

        _upsert_remind_log(db=db, friendship_id=friendship_id, currency=currency)
        sent_currencies.append(currency)

    pending_by_currency: dict[str, list[dict[str, Any]]] = {}
    for row in pending_rows:
        currency = str(row.get("currency", "ILS")).upper()
        pending_by_currency.setdefault(currency, []).append(row)

    for currency in sorted(pending_by_currency):
        if currency in sent_currencies:
            # Already sent a reminder in this currency in this run.
            continue

        allowed, retry_after = _is_reminder_allowed(
            db=db,
            friendship_id=friendship_id,
            currency=currency,
            cooldown_seconds=settings.REMIND_COOLDOWN_SECONDS,
        )
        if not allowed:
            cooldowns.append((currency, retry_after))
            continue

        rows = pending_by_currency[currency]
        preview = rows[:3]
        lines = []
        for row in preview:
            tx_id = str(row.get("id", "")).strip()
            tx_suffix = tx_id[-8:] if tx_id else "--------"
            amount = _to_decimal(row.get("amount"))
            lines.append(f"• `{tx_suffix}` {format_money(amount, currency)}")
        if len(rows) > len(preview):
            lines.append(f"• and {len(rows) - len(preview)} more pending")

        dm_text = (
            f"Reminder: {escape(requester_mention)} asked you to confirm pending offer(s).\n"
            + "\n".join(lines)
            + "\nOpen the bot and tap ✅ Confirm or ❌ Reject."
        )

        try:
            await message.bot.send_message(chat_id=int(target_tg_id), text=dm_text)
        except TelegramForbiddenError:
            logger.info("Pending-confirmation DM forbidden for telegram_user_id=%s", target_tg_id)
            await message.answer(
                "I can't DM this user (they blocked the bot or never started it). "
                "No reminder was sent."
            )
            return
        except Exception:
            logger.info(
                "Failed pending-confirmation reminder DM to telegram_user_id=%s",
                target_tg_id,
                exc_info=True,
            )
            await message.answer(
                "I couldn't send the reminder due to Telegram DM restrictions. "
                "Ask them to open the bot and allow messages."
            )
            return

        _upsert_remind_log(db=db, friendship_id=friendship_id, currency=currency)
        pending_sent = True
        if currency not in sent_currencies:
            sent_currencies.append(currency)

    cooldowns = sorted(set(cooldowns), key=lambda item: item[0])

    if not sent_currencies and cooldowns:
        await message.answer(
            "Reminder is on cooldown for: "
            + ", ".join(f"{cur} ({format_retry_after(wait)})" for cur, wait in cooldowns)
            + "."
        )
        return

    if not sent_currencies:
        await message.answer("No reminder was sent.")
        return

    base = f"Reminder sent to @{escape(target_username)}"
    if pending_sent and non_zero_rows:
        base += " (balances + pending confirmations)"
    elif pending_sent:
        base += " (pending confirmations)"

    if cooldowns:
        await message.answer(
            f"{base} for {', '.join(sent_currencies)}.\n"
            "Skipped due to cooldown: "
            + ", ".join(f"{cur} ({format_retry_after(wait)})" for cur, wait in cooldowns)
            + "."
        )
        return

    if len(sent_currencies) == 1:
        await message.answer(f"{base} for {sent_currencies[0]}.")
    else:
        await message.answer(f"{base} for {', '.join(sent_currencies)}.")


async def _start_remind_friend_picker(message: Message, db: Database) -> None:
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    friend_profiles = _list_friend_profiles_for_message(message=message, db=db)
    if not friend_profiles:
        await message.answer("No accepted friends yet.\nStart with /invite @friend.")
        return

    await message.answer(
        "Pick a friend to send a reminder:",
        reply_markup=_build_friend_keyboard(friend_profiles, _REMIND_PICK_PREFIX),
    )


def _list_friend_profiles_for_message(message: Message, db: Database) -> list[dict[str, Any]]:
    if message.from_user is None:
        return []

    viewer_profile = db.get_or_create_profile(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        display_name=_display_name(message),
    )
    viewer_id = str(viewer_profile.get("id", ""))
    if not viewer_id:
        return []

    friends_data = db.list_friends(viewer_id)
    friend_profiles: list[dict[str, Any]] = []
    for item in friends_data:
        friend_profile = item.get("friend_profile")
        if isinstance(friend_profile, dict) and friend_profile.get("id"):
            friend_profiles.append(friend_profile)

    friend_profiles.sort(key=lambda profile: _profile_label(profile).lower())
    return friend_profiles


def _build_friend_keyboard(friend_profiles: list[dict[str, Any]], prefix: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for profile in friend_profiles:
        profile_id = str(profile.get("id", ""))
        if not profile_id:
            continue
        label = _profile_label(profile)
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"{prefix}:{profile_id}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _parse_pick_callback(data: str | None, prefix: str) -> str | None:
    if not data:
        return None

    parts = data.split(":", maxsplit=1)
    if len(parts) != 2:
        return None

    received_prefix, profile_id = parts
    if received_prefix != prefix:
        return None

    try:
        return str(UUID(profile_id))
    except ValueError:
        return None


def _get_profile_by_id(db: Database, profile_id: str) -> dict[str, Any] | None:
    response = (
        db.client.table("profiles")
        .select("id,telegram_username,display_name,telegram_user_id")
        .eq("id", profile_id)
        .limit(1)
        .execute()
    )
    rows = response.data if isinstance(response.data, list) else []
    return rows[0] if rows else None


def _parse_remind_args(raw_args: str | None) -> tuple[str, str | None] | None:
    if not raw_args or not raw_args.strip():
        return None

    tokens = raw_args.strip().split()
    if len(tokens) not in {1, 2}:
        return None

    username = tokens[0].strip().lstrip("@").strip()
    if not username:
        return None

    if len(tokens) == 1:
        return username, None

    return username, normalize_currency_code(tokens[1])


def _get_accepted_friendship(db: Database, left_id: str, right_id: str) -> dict[str, Any] | None:
    friendship = _get_friendship_between(db=db, left_id=left_id, right_id=right_id)
    if friendship is None:
        return None
    return friendship if str(friendship.get("status", "")).lower() == "accepted" else None


def _get_friendship_between(db: Database, left_id: str, right_id: str) -> dict[str, Any] | None:
    user_low, user_high = canonical_pair(left_id, right_id)
    response = (
        db.client.table("friendships")
        .select("*")
        .eq("user_low", user_low)
        .eq("user_high", user_high)
        .limit(1)
        .execute()
    )
    rows = response.data if isinstance(response.data, list) else []
    return rows[0] if rows else None


def _friendship_error_text(target_username: str, friendship: dict[str, Any] | None) -> str:
    if friendship is None:
        return f"You are not connected with @{target_username}. Use /invite @{target_username} first."

    status = str(friendship.get("status", "")).lower()
    if status == "pending":
        return f"Friendship with @{target_username} is pending. Ask them to accept the invite."
    if status == "declined":
        return f"@{target_username} declined the previous invite. Send a new /invite @{target_username}."
    if status == "blocked":
        return f"@{target_username} blocked this connection."

    return f"@{target_username} is not an accepted friend yet."


def _get_balance_rows(
    db: Database,
    friendship_id: str,
    currency: str | None = None,
) -> list[dict[str, Any]]:
    query = (
        db.client.table("balances")
        .select("currency,net_amount")
        .eq("friendship_id", friendship_id)
    )
    if currency:
        query = query.eq("currency", currency.upper())
    response = query.execute()
    rows = response.data if isinstance(response.data, list) else []
    return [row for row in rows if isinstance(row, dict)]


def _get_pending_confirmation_rows(
    db: Database,
    friendship_id: str,
    created_by: str,
    currency: str | None = None,
) -> list[dict[str, Any]]:
    query = (
        db.client.table("transactions")
        .select("id,amount,currency")
        .eq("friendship_id", friendship_id)
        .eq("status", "pending")
        .eq("created_by", created_by)
        .order("created_at", desc=True)
        .limit(20)
    )
    if currency:
        query = query.eq("currency", currency.upper())
    response = query.execute()
    rows = response.data if isinstance(response.data, list) else []
    return [row for row in rows if isinstance(row, dict)]


def _is_reminder_allowed(
    db: Database,
    friendship_id: str,
    currency: str,
    cooldown_seconds: int,
) -> tuple[bool, int]:
    response = (
        db.client.table("remind_log")
        .select("last_remind_at")
        .eq("friendship_id", friendship_id)
        .eq("currency", currency.upper())
        .limit(1)
        .execute()
    )

    rows = response.data if isinstance(response.data, list) else []
    if not rows:
        return True, 0

    last_remind_at = _parse_datetime(rows[0].get("last_remind_at"))
    if last_remind_at is None:
        return True, 0

    now = datetime.now(timezone.utc)
    elapsed = (now - last_remind_at).total_seconds()
    remaining = int(cooldown_seconds - elapsed)
    if remaining > 0:
        return False, remaining

    return True, 0


def _upsert_remind_log(db: Database, friendship_id: str, currency: str) -> None:
    db.client.table("remind_log").upsert(
        {
            "friendship_id": friendship_id,
            "currency": currency.upper(),
            "last_remind_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="friendship_id,currency",
    ).execute()


def _they_owe_requester(
    *,
    requester_id: str,
    user_low: str,
    user_high: str,
    net_amount: Decimal,
) -> bool:
    if requester_id == user_low:
        return net_amount > Decimal("0")

    if requester_id == user_high:
        return net_amount < Decimal("0")

    return False


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    iso = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _display_name(message: Message) -> str | None:
    if message.from_user is None:
        return None

    first_name = (message.from_user.first_name or "").strip()
    last_name = (message.from_user.last_name or "").strip()
    full = f"{first_name} {last_name}".strip()
    return full or None


def _display_name_from_callback(callback: CallbackQuery) -> str | None:
    first_name = (callback.from_user.first_name or "").strip()
    last_name = (callback.from_user.last_name or "").strip()
    full = f"{first_name} {last_name}".strip()
    return full or None


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
    if telegram_user_id is not None:
        return f"user {telegram_user_id}"

    return "friend"
