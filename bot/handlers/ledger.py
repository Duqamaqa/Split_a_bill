from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from typing import Any
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..config import Settings
from ..currency import currencies_text, is_currency_token, normalize_currency_code
from ..db import Database
from ..logic import canonical_pair
from ..utils.formatting import (
    format_generic_balance_line,
    format_money,
    format_named_balance_line,
    format_transaction_history_line,
)

router = Router(name="ledger")
logger = logging.getLogger(__name__)
_TWO_DP = Decimal("0.01")
_DEFAULT_HISTORY_LIMIT = 20
_MAX_HISTORY_LIMIT = 100
_CURRENCY_SET_TEXT = currencies_text("|")
_OUT_PICK_PREFIX = "outpick"
_IN_PICK_PREFIX = "inpick"
_BAL_PICK_PREFIX = "balpick"
_HIST_PICK_PREFIX = "histpick"


class OutFlow(StatesGroup):
    waiting_amount = State()


class InFlow(StatesGroup):
    waiting_amount = State()


@router.message(Command("in"))
async def handle_in(
    message: Message,
    command: CommandObject,
    settings: Settings,
    db: Database,
    state: FSMContext,
) -> None:
    if not (command.args or "").strip():
        await _start_in_friend_picker(message=message, db=db, state=state)
        return

    await state.clear()
    await _handle_entry(message, command, settings, db, direction="in")


@router.message(Command("out"))
async def handle_out(
    message: Message,
    command: CommandObject,
    settings: Settings,
    db: Database,
    state: FSMContext,
) -> None:
    if not (command.args or "").strip():
        await _start_out_friend_picker(message=message, db=db, state=state)
        return

    await state.clear()
    await _handle_entry(message, command, settings, db, direction="out")


@router.callback_query(F.data.startswith(f"{_OUT_PICK_PREFIX}:"))
async def handle_out_friend_pick(
    callback: CallbackQuery,
    db: Database,
    state: FSMContext,
) -> None:
    if callback.from_user is None:
        await callback.answer("Unable to resolve user.", show_alert=True)
        return

    friend_id = _parse_pick_callback(callback.data, _OUT_PICK_PREFIX)
    if friend_id is None:
        await callback.answer("Invalid selection.", show_alert=True)
        return

    actor_profile = db.get_or_create_profile(
        telegram_user_id=callback.from_user.id,
        username=callback.from_user.username,
        display_name=_display_name_from_callback(callback),
    )
    actor_id = str(actor_profile.get("id", ""))
    if not actor_id:
        await callback.answer("Profile data is incomplete.", show_alert=True)
        return

    friends_data = db.list_friends(actor_id)
    selected_profile: dict[str, Any] | None = None
    for item in friends_data:
        friend_profile = item.get("friend_profile") or {}
        if str(friend_profile.get("id", "")) == friend_id:
            selected_profile = friend_profile
            break

    if selected_profile is None:
        await callback.answer("Friend not found in your accepted list.", show_alert=True)
        return

    selected_label = _profile_label(selected_profile, "friend")
    selected_username = str(selected_profile.get("telegram_username", "")).strip()

    await state.set_state(OutFlow.waiting_amount)
    await state.update_data(
        out_friend_id=friend_id,
        out_friend_username=selected_username,
        out_friend_label=selected_label,
    )

    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"Selected: <b>{escape(selected_label)}</b>\n"
            "Now send amount as:\n"
            "• 100\n"
            "• 100 USD\n"
            "• 100 USD dinner\n\n"
            "Send /cancel to cancel."
        )
    await callback.answer("Friend selected")


@router.callback_query(F.data.startswith(f"{_IN_PICK_PREFIX}:"))
async def handle_in_friend_pick(
    callback: CallbackQuery,
    db: Database,
    state: FSMContext,
) -> None:
    if callback.from_user is None:
        await callback.answer("Unable to resolve user.", show_alert=True)
        return

    friend_id = _parse_pick_callback(callback.data, _IN_PICK_PREFIX)
    if friend_id is None:
        await callback.answer("Invalid selection.", show_alert=True)
        return

    actor_profile = db.get_or_create_profile(
        telegram_user_id=callback.from_user.id,
        username=callback.from_user.username,
        display_name=_display_name_from_callback(callback),
    )
    actor_id = str(actor_profile.get("id", ""))
    if not actor_id:
        await callback.answer("Profile data is incomplete.", show_alert=True)
        return

    friends_data = db.list_friends(actor_id)
    selected_profile: dict[str, Any] | None = None
    for item in friends_data:
        friend_profile = item.get("friend_profile") or {}
        if str(friend_profile.get("id", "")) == friend_id:
            selected_profile = friend_profile
            break

    if selected_profile is None:
        await callback.answer("Friend not found in your accepted list.", show_alert=True)
        return

    selected_label = _profile_label(selected_profile, "friend")
    selected_username = str(selected_profile.get("telegram_username", "")).strip()

    await state.set_state(InFlow.waiting_amount)
    await state.update_data(
        in_friend_id=friend_id,
        in_friend_username=selected_username,
        in_friend_label=selected_label,
    )

    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"Selected: <b>{escape(selected_label)}</b>\n"
            "Now send amount as:\n"
            "• 100\n"
            "• 100 USD\n"
            "• 100 USD dinner\n\n"
            "Send /cancel to cancel."
        )
    await callback.answer("Friend selected")


@router.message(StateFilter(OutFlow.waiting_amount), Command("cancel"))
async def handle_out_flow_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Canceled.")


@router.message(StateFilter(OutFlow.waiting_amount))
async def handle_out_amount_step(
    message: Message,
    settings: Settings,
    db: Database,
    state: FSMContext,
) -> None:
    await _handle_guided_entry_amount_step(
        message=message,
        settings=settings,
        db=db,
        state=state,
        direction="out",
    )


@router.message(StateFilter(InFlow.waiting_amount), Command("cancel"))
async def handle_in_flow_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Canceled.")


@router.message(StateFilter(InFlow.waiting_amount))
async def handle_in_amount_step(
    message: Message,
    settings: Settings,
    db: Database,
    state: FSMContext,
) -> None:
    await _handle_guided_entry_amount_step(
        message=message,
        settings=settings,
        db=db,
        state=state,
        direction="in",
    )


@router.callback_query(F.data.startswith(f"{_BAL_PICK_PREFIX}:"))
async def handle_balance_friend_pick(callback: CallbackQuery, db: Database) -> None:
    if callback.from_user is None:
        await callback.answer("Unable to resolve user.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Message context is missing.", show_alert=True)
        return

    friend_id = _parse_pick_callback(callback.data, _BAL_PICK_PREFIX)
    if friend_id is None:
        await callback.answer("Invalid selection.", show_alert=True)
        return

    viewer_profile = db.get_or_create_profile(
        telegram_user_id=callback.from_user.id,
        username=callback.from_user.username,
        display_name=_display_name_from_callback(callback),
    )
    friend_profile = _get_profile_by_id(db=db, profile_id=friend_id)
    if friend_profile is None:
        await callback.answer("Friend not found.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await _send_balance_summary(
        message=callback.message,
        db=db,
        viewer_profile=viewer_profile,
        friend_profile=friend_profile,
        requested_currency=None,
    )
    await callback.answer("Done")


@router.callback_query(F.data.startswith(f"{_HIST_PICK_PREFIX}:"))
async def handle_history_friend_pick(callback: CallbackQuery, db: Database) -> None:
    if callback.from_user is None:
        await callback.answer("Unable to resolve user.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Message context is missing.", show_alert=True)
        return

    friend_id = _parse_pick_callback(callback.data, _HIST_PICK_PREFIX)
    if friend_id is None:
        await callback.answer("Invalid selection.", show_alert=True)
        return

    viewer_profile = db.get_or_create_profile(
        telegram_user_id=callback.from_user.id,
        username=callback.from_user.username,
        display_name=_display_name_from_callback(callback),
    )
    friend_profile = _get_profile_by_id(db=db, profile_id=friend_id)
    if friend_profile is None:
        await callback.answer("Friend not found.", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await _send_history_summary(
        message=callback.message,
        db=db,
        viewer_profile=viewer_profile,
        friend_profile=friend_profile,
        limit=_DEFAULT_HISTORY_LIMIT,
    )
    await callback.answer("Done")


async def _handle_guided_entry_amount_step(
    *,
    message: Message,
    settings: Settings,
    db: Database,
    state: FSMContext,
    direction: str,
) -> None:
    if direction not in {"in", "out"}:
        await state.clear()
        await message.answer("Invalid flow state. Please run the command again.")
        return

    if message.from_user is None:
        await message.answer("Unable to resolve user context.")
        return

    raw_text = (message.text or "").strip()
    if not raw_text:
        await message.answer("Please send amount, for example: 100 USD dinner")
        return

    try:
        amount, explicit_currency, note = _parse_amount_currency_note(raw_text)
    except ValueError as exc:
        await message.answer(
            f"{exc}\n"
            "Send amount as: 100 | 100 USD | 100 USD dinner\n"
            "Or send /cancel."
        )
        return

    data = await state.get_data()
    state_key = "out_friend_id" if direction == "out" else "in_friend_id"
    friend_id = str(data.get(state_key, ""))
    if not friend_id:
        await state.clear()
        await message.answer(f"Session expired. Please run /{direction} again.")
        return

    creator_profile = db.get_or_create_profile(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        display_name=_display_name(message),
    )
    creator_id = str(creator_profile.get("id", ""))
    if not creator_id:
        await state.clear()
        await message.answer(f"Profile data is incomplete. Try /{direction} again.")
        return

    friend_profile = _get_profile_by_id(db=db, profile_id=friend_id)
    if friend_profile is None:
        await state.clear()
        await message.answer(f"Selected friend no longer exists. Please run /{direction} again.")
        return

    if creator_id == friend_id:
        await state.clear()
        await message.answer("You cannot create a transaction with yourself.")
        return

    friendship = _get_accepted_friendship(db=db, left_id=creator_id, right_id=friend_id)
    if friendship is None:
        friend_username = str(friend_profile.get("telegram_username", "")).strip() or "friend"
        friendship_any = _get_friendship_between(db=db, left_id=creator_id, right_id=friend_id)
        await state.clear()
        await message.answer(
            _friendship_error_text(target_username=friend_username, friendship=friendship_any)
        )
        return

    friendship_id = str(friendship.get("id", ""))
    if not friendship_id:
        await state.clear()
        await message.answer("Friendship data is invalid.")
        return

    tx_currency = _resolve_profile_currency(
        profile=creator_profile,
        fallback=settings.DEFAULT_CURRENCY,
        explicit=explicit_currency,
    )

    try:
        if direction == "out":
            tx = db.create_confirmed_transaction(
                friendship_id=friendship_id,
                created_by=creator_id,
                direction="out",
                amount=amount,
                currency=tx_currency,
                note=note,
            )
        else:
            tx = db.create_pending_transaction(
                friendship_id=friendship_id,
                created_by=creator_id,
                direction="in",
                amount=amount,
                currency=tx_currency,
                note=note,
            )
    except Exception:
        logger.exception("Failed to create guided /%s transaction", direction)
        await message.answer(f"Failed to create transaction. Try /{direction} again.")
        await state.clear()
        return

    tx_id = str(tx.get("id", ""))
    if not tx_id:
        await state.clear()
        await message.answer("Transaction created, but ID is missing.")
        return

    if direction == "out":
        await _notify_out_recorded(
            message=message,
            creator_profile=creator_profile,
            friend_profile=friend_profile,
            amount=amount,
            currency=tx_currency,
            note=note,
        )
        await state.clear()
        return

    target_username = str(friend_profile.get("telegram_username", "")).strip()
    await _notify_pending_confirmation(
        message=message,
        creator_profile=creator_profile,
        friend_profile=friend_profile,
        target_username=target_username,
        amount=amount,
        currency=tx_currency,
        note=note,
        tx_id=tx_id,
        direction="in",
    )

    await state.clear()


@router.message(Command("setcurrency"))
async def handle_setcurrency(
    message: Message,
    command: CommandObject,
    settings: Settings,
    db: Database,
) -> None:
    """Persist caller's default currency for /in and /out when currency is omitted."""
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    raw_args = (command.args or "").strip()
    if not raw_args:
        await message.answer(
            f"Usage: /setcurrency <{_CURRENCY_SET_TEXT}>\n"
            "Example: /setcurrency RUB"
        )
        return

    tokens = raw_args.split()
    if len(tokens) != 1:
        await message.answer(
            f"Usage: /setcurrency <{_CURRENCY_SET_TEXT}>\n"
            "Example: /setcurrency RUB"
        )
        return

    try:
        chosen_currency = normalize_currency_code(tokens[0])
    except ValueError as exc:
        await message.answer(str(exc))
        return

    profile = db.get_or_create_profile(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        display_name=_display_name(message),
    )

    profile_id = str(profile.get("id", ""))
    if not profile_id:
        await message.answer("Profile data is incomplete. Try again.")
        return

    try:
        updated = db.set_profile_default_currency(profile_id=profile_id, currency=chosen_currency)
    except Exception:
        logger.exception("Failed to set default currency for profile_id=%s", profile_id)
        await message.answer(
            "Couldn't save default currency. "
            "Run the latest Supabase migration and try again."
        )
        return

    if updated is None:
        await message.answer("Failed to update your default currency. Try again.")
        return

    await message.answer(
        f"Default currency set to <b>{chosen_currency}</b>.\n"
        "/in and /out will use it when currency is omitted."
    )


@router.message(Command("history"))
async def handle_history(message: Message, command: CommandObject, db: Database) -> None:
    """Show recent transactions with a specific accepted friend."""
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    if not (command.args or "").strip():
        await _start_history_friend_picker(message=message, db=db)
        return

    parsed = _parse_history_args(command.args)
    if parsed is None:
        await message.answer("Usage: /history @friend [limit]\nExample: /history @friend 25")
        return

    target_username, limit = parsed

    viewer_profile = db.get_or_create_profile(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        display_name=_display_name(message),
    )

    friend_profile = db.get_profile_by_username(target_username)
    if friend_profile is None:
        await message.answer(f"I couldn't find @{target_username}. Ask them to run /start first.")
        return

    await _send_history_summary(
        message=message,
        db=db,
        viewer_profile=viewer_profile,
        friend_profile=friend_profile,
        limit=limit,
    )


@router.message(Command("balance"))
async def handle_balance(message: Message, command: CommandObject, db: Database) -> None:
    """Show current balance summary with a specific accepted friend."""
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    if not (command.args or "").strip():
        await _start_balance_friend_picker(message=message, db=db)
        return

    try:
        parsed = _parse_balance_args(command.args)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    if parsed is None:
        await message.answer(
            f"Usage: /balance @friend [currency]\n"
            f"Example: /balance @friend RUB"
        )
        return
    target_username, requested_currency = parsed

    viewer_profile = db.get_or_create_profile(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        display_name=_display_name(message),
    )

    friend_profile = db.get_profile_by_username(target_username)
    if friend_profile is None:
        await message.answer(f"I couldn't find @{target_username}. Ask them to run /start first.")
        return

    await _send_balance_summary(
        message=message,
        db=db,
        viewer_profile=viewer_profile,
        friend_profile=friend_profile,
        requested_currency=requested_currency,
    )


@router.message(Command("friends"))
async def handle_friends(message: Message, db: Database) -> None:
    """List all accepted friends and per-currency balance status lines."""
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    viewer_profile = db.get_or_create_profile(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        display_name=_display_name(message),
    )

    # Efficient path: db.list_friends fetches accepted friendships and balances in batch.
    friends_data = db.list_friends(str(viewer_profile["id"]))
    if not friends_data:
        await message.answer("No accepted friends yet.\nStart with /invite @friend.")
        return

    blocks: list[str] = [
        "<b>Accepted friends</b>\n"
        "Legend: 🟢 friend owes you | 🔴 you owe friend | ⚪ settled"
    ]

    sorted_friends = sorted(
        friends_data,
        key=lambda item: _profile_label(item.get("friend_profile"), "friend").lower(),
    )

    for item in sorted_friends:
        friend_profile = item.get("friend_profile") or {}
        friend_label = _profile_label(friend_profile, "friend")
        balance_rows = item.get("balance") or []
        non_zero_rows = [
            row
            for row in balance_rows
            if _to_decimal(row.get("they_owe_you")) > Decimal("0.00")
            or _to_decimal(row.get("you_owe")) > Decimal("0.00")
        ]

        lines: list[str] = [f"<b>{escape(friend_label)}</b>"]
        if not non_zero_rows:
            lines.append("⚪ Settled")
        else:
            for row in sorted(non_zero_rows, key=lambda balance: str(balance.get("currency", ""))):
                lines.append(
                    format_generic_balance_line(
                        currency=str(row.get("currency", "ILS")),
                        they_owe_you=_to_decimal(row.get("they_owe_you")),
                        you_owe=_to_decimal(row.get("you_owe")),
                    )
                )

        blocks.append("\n".join(lines))

    await message.answer("\n\n".join(blocks))


async def _handle_entry(
    message: Message,
    command: CommandObject,
    settings: Settings,
    db: Database,
    direction: str,
) -> None:
    """Create a pending `/in` or `/out` transaction and request counterparty confirmation."""
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    try:
        parsed = _parse_peer_amount_note(command.args)
    except ValueError as exc:
        await message.answer(
            f"{exc}\n"
            f"Usage: /{direction} @friend <amount> [currency] [note...]\n"
            f"Examples:\n"
            f"/{direction} @friend 42.50\n"
            f"/{direction} @friend 42.50 RUB dinner"
        )
        return

    target_username, amount, explicit_currency, note = parsed

    creator_profile = db.get_or_create_profile(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        display_name=_display_name(message),
    )

    friend_profile = db.get_profile_by_username(target_username)
    if friend_profile is None:
        await message.answer(f"I couldn't find @{target_username}. Ask them to run /start first.")
        return

    creator_id = str(creator_profile.get("id", ""))
    friend_id = str(friend_profile.get("id", ""))
    if not creator_id or not friend_id:
        await message.answer("Profile data is incomplete. Try again.")
        return

    if creator_id == friend_id:
        await message.answer("You cannot create a transaction with yourself.")
        return

    friendship = _get_accepted_friendship(db=db, left_id=creator_id, right_id=friend_id)
    if friendship is None:
        friendship_any = _get_friendship_between(db=db, left_id=creator_id, right_id=friend_id)
        await message.answer(_friendship_error_text(target_username=target_username, friendship=friendship_any))
        return

    friendship_id = str(friendship.get("id", ""))
    if not friendship_id:
        await message.answer("Friendship data is invalid.")
        return

    tx_currency = _resolve_profile_currency(
        profile=creator_profile,
        fallback=settings.DEFAULT_CURRENCY,
        explicit=explicit_currency,
    )

    try:
        if direction == "out":
            tx = db.create_confirmed_transaction(
                friendship_id=friendship_id,
                created_by=creator_id,
                direction="out",
                amount=amount,
                currency=tx_currency,
                note=note,
            )
        else:
            tx = db.create_pending_transaction(
                friendship_id=friendship_id,
                created_by=creator_id,
                direction="in",
                amount=amount,
                currency=tx_currency,
                note=note,
            )
    except Exception:
        logger.exception("Failed to create %s transaction", direction)
        await message.answer("Failed to create transaction. Try again.")
        return

    tx_id = str(tx.get("id", ""))
    if not tx_id:
        await message.answer("Transaction created, but ID is missing.")
        return

    if direction == "out":
        await _notify_out_recorded(
            message=message,
            creator_profile=creator_profile,
            friend_profile=friend_profile,
            amount=amount,
            currency=tx_currency,
            note=note,
        )
        return

    await _notify_pending_confirmation(
        message=message,
        creator_profile=creator_profile,
        friend_profile=friend_profile,
        target_username=target_username,
        amount=amount,
        currency=tx_currency,
        note=note,
        tx_id=tx_id,
        direction="in",
    )


def _tx_keyboard(tx_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Confirm", callback_data=f"tx:confirm:{tx_id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"tx:reject:{tx_id}"),
            ]
        ]
    )


def _guided_action_line(direction: str) -> str:
    if direction == "out":
        return "Claim: I paid for you (you owe me)."
    return "Claim: You paid for me (I owe you)."


async def _notify_pending_confirmation(
    *,
    message: Message,
    creator_profile: dict[str, Any],
    friend_profile: dict[str, Any],
    target_username: str,
    amount: Decimal,
    currency: str,
    note: str,
    tx_id: str,
    direction: str,
) -> None:
    safe_target_username = target_username.strip()
    mention = (
        f"@{safe_target_username}"
        if safe_target_username
        else _profile_label(friend_profile, "friend")
    )

    await message.answer(
        "Transaction submitted for confirmation.\n"
        f"To: {escape(mention)}\n"
        f"Amount: {format_money(amount, currency)}\n"
        "I sent them Confirm / Reject buttons."
    )

    counterparty_tg_id = friend_profile.get("telegram_user_id")
    if not counterparty_tg_id:
        await message.answer("Counterparty has no Telegram ID saved, cannot send confirmation DM.")
        return

    creator_label = _profile_label(creator_profile, "friend")
    note_suffix = f"\nNote: {escape(note)}" if note else ""
    dm_text = (
        f"<b>{escape(creator_label)}</b> submitted a transaction with you.\n"
        f"{_guided_action_line(direction)}\n"
        f"Amount: {format_money(amount, currency)}"
        f"{note_suffix}\n\n"
        "Confirm only if this is correct:"
    )

    try:
        await message.bot.send_message(
            chat_id=int(counterparty_tg_id),
            text=dm_text,
            reply_markup=_tx_keyboard(tx_id),
        )
    except TelegramForbiddenError:
        logger.info("Counterparty DM forbidden telegram_user_id=%s", counterparty_tg_id)
        await message.answer(
            "Transaction is pending, but I cannot DM the counterparty "
            "(they blocked the bot or never started it)."
        )
    except Exception:
        logger.info("Failed to DM tx confirmation to telegram_user_id=%s", counterparty_tg_id, exc_info=True)
        await message.answer(
            "Transaction is pending, but I could not DM the counterparty. "
            "They need to start the bot and allow DMs."
        )


async def _notify_out_recorded(
    *,
    message: Message,
    creator_profile: dict[str, Any],
    friend_profile: dict[str, Any],
    amount: Decimal,
    currency: str,
    note: str,
) -> None:
    target_username = str(friend_profile.get("telegram_username", "")).strip()
    target_label = _profile_label(friend_profile, "friend")
    mention = f"@{target_username}" if target_username else target_label

    await message.answer(
        "Transaction recorded.\n"
        f"To: {escape(mention)}\n"
        f"Amount: {format_money(amount, currency)}\n"
        "No confirmation is needed."
    )

    counterparty_tg_id = friend_profile.get("telegram_user_id")
    if not counterparty_tg_id:
        await message.answer("Transaction recorded, but the friend has no Telegram ID saved for DM.")
        return

    creator_label = _profile_label(creator_profile, "friend")
    note_suffix = f"\nNote: {escape(note)}" if note else ""
    dm_text = (
        f"<b>{escape(creator_label)}</b> sent you money.\n"
        f"Amount: {format_money(amount, currency)}"
        f"{note_suffix}\n"
        "Recorded in Owee. No action needed."
    )

    try:
        await message.bot.send_message(
            chat_id=int(counterparty_tg_id),
            text=dm_text,
        )
    except TelegramForbiddenError:
        logger.info("Counterparty DM forbidden telegram_user_id=%s", counterparty_tg_id)
        await message.answer(
            "Transaction is recorded, but I cannot DM the friend "
            "(they blocked the bot or never started it)."
        )
    except Exception:
        logger.info(
            "Failed to DM out-transaction notification to telegram_user_id=%s",
            counterparty_tg_id,
            exc_info=True,
        )
        await message.answer(
            "Transaction is recorded, but I could not DM the friend. "
            "They need to start the bot and allow messages."
        )


async def _send_history_summary(
    *,
    message: Message,
    db: Database,
    viewer_profile: dict[str, Any],
    friend_profile: dict[str, Any],
    limit: int,
) -> None:
    viewer_id = str(viewer_profile.get("id", ""))
    friend_id = str(friend_profile.get("id", ""))
    if not viewer_id or not friend_id:
        await message.answer("Profile data is incomplete. Try again.")
        return

    friend_username = str(friend_profile.get("telegram_username", "")).strip() or "friend"
    friendship = _get_accepted_friendship(db=db, left_id=viewer_id, right_id=friend_id)
    if friendship is None:
        friendship_any = _get_friendship_between(db=db, left_id=viewer_id, right_id=friend_id)
        await message.answer(_friendship_error_text(target_username=friend_username, friendship=friendship_any))
        return

    friendship_id = str(friendship.get("id", ""))
    transactions = db.list_transactions(friendship_id=friendship_id, limit=limit)
    friend_label = _profile_label(friend_profile, None)

    if not transactions:
        await message.answer(
            f"No transactions yet with {escape(friend_label)}.\n"
            f"Try /out @{friend_username} 100 or /in @{friend_username} 100."
        )
        return

    profiles_by_id = _get_profiles_by_ids(db, [viewer_id, friend_id])
    lines = [f"<b>History with {escape(friend_label)}</b>"]

    for tx in transactions:
        creator_id = str(tx.get("created_by", ""))
        if creator_id == viewer_id:
            creator_label = "You"
        else:
            creator_label = _profile_label(profiles_by_id.get(creator_id), "Friend")
        lines.append(format_transaction_history_line(tx=tx, creator_label=creator_label))

    await message.answer("\n".join(lines))


async def _send_balance_summary(
    *,
    message: Message,
    db: Database,
    viewer_profile: dict[str, Any],
    friend_profile: dict[str, Any],
    requested_currency: str | None,
) -> None:
    viewer_id = str(viewer_profile.get("id", ""))
    friend_id = str(friend_profile.get("id", ""))
    if not viewer_id or not friend_id:
        await message.answer("Profile data is incomplete. Try again.")
        return

    friend_username = str(friend_profile.get("telegram_username", "")).strip() or "friend"
    friendship = _get_accepted_friendship(db=db, left_id=viewer_id, right_id=friend_id)
    if friendship is None:
        friendship_any = _get_friendship_between(db=db, left_id=viewer_id, right_id=friend_id)
        await message.answer(_friendship_error_text(target_username=friend_username, friendship=friendship_any))
        return

    friendship_id = str(friendship.get("id", ""))
    balance_rows = _get_balance_rows(
        db=db,
        friendship_id=friendship_id,
        currency=requested_currency,
    )
    balance_rows = [
        row for row in balance_rows if _to_decimal(row.get("net_amount")) != Decimal("0.00")
    ]
    friend_label = _profile_label(friend_profile, None)

    lines = [f"<b>Balance with {escape(friend_label)}</b>"]
    if not balance_rows:
        lines.append("⚪ Settled (no outstanding balance)")
        await message.answer("\n".join(lines))
        return

    user_low = str(friendship.get("user_low", ""))
    user_high = str(friendship.get("user_high", ""))

    for row in sorted(balance_rows, key=lambda item: str(item.get("currency", ""))):
        currency = str(row.get("currency", "ILS")).upper()
        net_amount = _to_decimal(row.get("net_amount"))
        they_owe_you, you_owe = _viewer_amounts_from_net(
            viewer_id=viewer_id,
            user_low=user_low,
            user_high=user_high,
            net_amount=net_amount,
        )
        lines.append(
            format_named_balance_line(
                friend_label=friend_label,
                currency=currency,
                they_owe_you=they_owe_you,
                you_owe=you_owe,
            )
        )

    await message.answer("\n".join(lines))


async def _start_out_friend_picker(message: Message, db: Database, state: FSMContext) -> None:
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    friend_profiles = _list_friend_profiles_for_message(message=message, db=db)
    if not friend_profiles:
        await message.answer("No accepted friends yet.\nStart with /invite @friend.")
        return

    await state.clear()
    await message.answer(
        "Who did you pay for?",
        reply_markup=_build_friend_keyboard(friend_profiles, _OUT_PICK_PREFIX),
    )


async def _start_in_friend_picker(message: Message, db: Database, state: FSMContext) -> None:
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    friend_profiles = _list_friend_profiles_for_message(message=message, db=db)
    if not friend_profiles:
        await message.answer("No accepted friends yet.\nStart with /invite @friend.")
        return

    await state.clear()
    await message.answer(
        "Who paid for you?",
        reply_markup=_build_friend_keyboard(friend_profiles, _IN_PICK_PREFIX),
    )


async def _start_balance_friend_picker(message: Message, db: Database) -> None:
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    friend_profiles = _list_friend_profiles_for_message(message=message, db=db)
    if not friend_profiles:
        await message.answer("No accepted friends yet.\nStart with /invite @friend.")
        return

    await message.answer(
        "Pick a friend to see the balance:",
        reply_markup=_build_friend_keyboard(friend_profiles, _BAL_PICK_PREFIX),
    )


async def _start_history_friend_picker(message: Message, db: Database) -> None:
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    friend_profiles = _list_friend_profiles_for_message(message=message, db=db)
    if not friend_profiles:
        await message.answer("No accepted friends yet.\nStart with /invite @friend.")
        return

    await message.answer(
        f"Pick a friend to see last {_DEFAULT_HISTORY_LIMIT} transactions:",
        reply_markup=_build_friend_keyboard(friend_profiles, _HIST_PICK_PREFIX),
    )


def _build_friend_keyboard(friend_profiles: list[dict[str, Any]], prefix: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for profile in friend_profiles:
        profile_id = str(profile.get("id", ""))
        if not profile_id:
            continue
        label = _profile_label(profile, "friend")
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{prefix}:{profile_id}",
                )
            ]
        )
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

    friend_profiles.sort(key=lambda profile: _profile_label(profile, "friend").lower())
    return friend_profiles


def _parse_amount_currency_note(raw_text: str) -> tuple[Decimal, str | None, str]:
    tokens = raw_text.strip().split()
    if not tokens:
        raise ValueError("Missing amount.")

    amount = _parse_amount(tokens[0])
    currency: str | None = None
    note_start = 1

    if len(tokens) > 1 and is_currency_token(tokens[1]):
        currency = normalize_currency_code(tokens[1])
        note_start = 2

    note = " ".join(tokens[note_start:]).strip()
    return amount, currency, note


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


def _parse_peer_amount_note(raw_args: str | None) -> tuple[str, Decimal, str | None, str]:
    if not raw_args or not raw_args.strip():
        raise ValueError("Missing arguments.")

    tokens = raw_args.strip().split()
    if len(tokens) < 2:
        raise ValueError("Missing amount.")

    username = _normalize_username(tokens[0])
    if username is None:
        raise ValueError("Invalid username.")

    amount = _parse_amount(tokens[1])

    currency: str | None = None
    note_start = 2
    if len(tokens) > 2 and is_currency_token(tokens[2]):
        try:
            currency = normalize_currency_code(tokens[2])
            note_start = 3
        except ValueError:
            # Keep 3-letter note words valid when additional note text exists.
            if len(tokens) == 3:
                raise

    note = " ".join(tokens[note_start:]).strip()
    return username, amount, currency, note


def _parse_history_args(raw_args: str | None) -> tuple[str, int] | None:
    if not raw_args or not raw_args.strip():
        return None

    tokens = raw_args.strip().split()
    if len(tokens) > 2:
        return None

    username = _normalize_username(tokens[0])
    if username is None:
        return None

    limit = _DEFAULT_HISTORY_LIMIT
    if len(tokens) == 2:
        try:
            parsed_limit = int(tokens[1])
        except ValueError:
            return None
        if parsed_limit <= 0:
            return None
        limit = min(parsed_limit, _MAX_HISTORY_LIMIT)

    return username, limit


def _parse_balance_args(raw_args: str | None) -> tuple[str, str | None] | None:
    if not raw_args or not raw_args.strip():
        return None

    tokens = raw_args.strip().split()
    if len(tokens) not in {1, 2}:
        return None

    username = _normalize_username(tokens[0])
    if username is None:
        return None

    if len(tokens) == 1:
        return username, None

    return username, normalize_currency_code(tokens[1])


def _normalize_username(token: str) -> str | None:
    cleaned = token.strip().lstrip("@").strip()
    return cleaned if cleaned else None


def _parse_amount(value: str) -> Decimal:
    raw = value.strip().replace(" ", "")
    if not raw:
        raise ValueError("Missing amount")

    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            normalized = raw.replace(".", "").replace(",", ".")
        else:
            normalized = raw.replace(",", "")
    elif "," in raw:
        if raw.count(",") == 1 and len(raw.split(",", maxsplit=1)[1]) <= 2:
            normalized = raw.replace(",", ".")
        else:
            normalized = raw.replace(",", "")
    else:
        normalized = raw

    try:
        amount = Decimal(normalized).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid amount") from exc

    if amount <= 0:
        raise ValueError("Amount must be greater than zero")
    return amount


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


def _get_accepted_friendship(db: Database, left_id: str, right_id: str) -> dict[str, Any] | None:
    friendship = _get_friendship_between(db=db, left_id=left_id, right_id=right_id)
    if friendship is None:
        return None
    return friendship if str(friendship.get("status", "")).lower() == "accepted" else None


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


def _resolve_profile_currency(
    *,
    profile: dict[str, Any],
    fallback: str,
    explicit: str | None,
) -> str:
    if explicit:
        return explicit

    profile_currency = str(profile.get("default_currency", "")).strip() or None
    if profile_currency:
        try:
            return normalize_currency_code(profile_currency)
        except ValueError:
            pass

    return normalize_currency_code(fallback)


def _get_profiles_by_ids(db: Database, profile_ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = sorted({profile_id for profile_id in profile_ids if profile_id})
    if not ids:
        return {}

    response = (
        db.client.table("profiles")
        .select("id,telegram_username,display_name,telegram_user_id")
        .in_("id", ids)
        .execute()
    )
    rows = response.data if isinstance(response.data, list) else []
    return {str(row.get("id")): row for row in rows if isinstance(row, dict) and row.get("id")}


def _viewer_amounts_from_net(
    *,
    viewer_id: str,
    user_low: str,
    user_high: str,
    net_amount: Decimal,
) -> tuple[Decimal, Decimal]:
    if viewer_id == user_low:
        they_owe_you = max(net_amount, Decimal("0.00"))
        you_owe = max(-net_amount, Decimal("0.00"))
        return they_owe_you, you_owe

    if viewer_id == user_high:
        they_owe_you = max(-net_amount, Decimal("0.00"))
        you_owe = max(net_amount, Decimal("0.00"))
        return they_owe_you, you_owe

    raise ValueError("Viewer is not part of friendship")


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


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


def _profile_label(profile: dict[str, Any] | None, fallback: str | None) -> str:
    if not profile:
        return fallback or "friend"

    display_name = str(profile.get("display_name", "")).strip()
    if display_name:
        return display_name

    username = str(profile.get("telegram_username", "")).strip()
    if username:
        return f"@{username}"

    telegram_user_id = profile.get("telegram_user_id")
    if telegram_user_id is not None:
        return f"user {telegram_user_id}"

    return fallback or "friend"
