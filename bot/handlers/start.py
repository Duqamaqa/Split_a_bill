from __future__ import annotations

import logging
from datetime import datetime, timezone
from html import escape
from typing import Any
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database

router = Router(name="start")
logger = logging.getLogger(__name__)

INVITE_PREFIX = "inv_"
CALLBACK_PREFIX = "invite"
CALLBACK_ACCEPT = "accept"
CALLBACK_DECLINE = "decline"
CALLBACK_BLOCK = "block"
HELP_TEXT = (
    "<b>Owee Help</b>\n\n"
    "<b>What commands mean</b>\n"
    "• /out @friend 100 - You paid for friend, so friend owes you (saved instantly).\n"
    "• /in @friend 100 - Friend paid for you, so you owe friend.\n\n"
    "<b>Guided mode (buttons)</b>\n"
    "• /out - Pick friend, then send amount.\n"
    "• /in - Pick friend, then send amount.\n"
    "• /balance - Pick friend to view balance.\n"
    "• /history - Pick friend to view latest transactions.\n"
    "• /remind - Pick friend and send reminder.\n\n"
    "<b>Start in 4 steps</b>\n"
    "1. Connect: /invite @friend\n"
    "2. Pick default currency (optional): /setcurrency RUB\n"
    "3. Add transactions: /out @friend 120 dinner\n"
    "4. Check status: /friends or /balance @friend\n\n"
    "<b>Useful examples</b>\n"
    "/out\n"
    "/in\n"
    "100 RUB dinner\n"
    "/balance\n"
    "/history\n"
    "/remind\n"
    "/in @friend 42.50 USD lunch\n"
    "/balance @friend RUB\n"
    "/history @friend 20\n"
    "/remind @friend USD\n\n"
    "<b>Supported currencies</b>\n"
    "ILS, USD, EUR, RUB"
)


@router.message(CommandStart())
async def handle_start(
    message: Message,
    command: CommandObject,
    db: Database,
) -> None:
    """Register/update the caller profile and optionally process invite deep-links."""
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    profile = db.get_or_create_profile(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        display_name=_display_name(message),
    )

    deep_link_payload = (command.args or "").strip()

    await message.answer(
        "<b>Welcome to Owee</b>\n"
        "Track who owes who without spreadsheets.\n\n"
        "<b>Quick start</b>\n"
        "• /invite @friend\n"
        "• /out or /in (pick friend from buttons)\n"
        "• then send: 100 or 100 USD dinner\n"
        "• /balance, /history, /remind (friend picker buttons)\n"
        "• /out @friend 100 (you paid, friend owes you)\n"
        "• /in @friend 45 (friend paid, you owe friend)\n"
        "• /friends\n\n"
        "Use /help for full step-by-step examples."
    )

    if deep_link_payload.startswith(INVITE_PREFIX):
        invite_code = deep_link_payload[len(INVITE_PREFIX) :].strip()
        if not invite_code:
            await message.answer("Invalid invite link.")
            return
        await _handle_invite_start(
            message=message,
            db=db,
            invite_code=invite_code,
            invitee_profile=profile,
        )


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    """Show command usage examples."""
    await message.answer(HELP_TEXT)


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:"))
async def handle_invite_callback(callback: CallbackQuery, db: Database) -> None:
    """Handle invite accept/decline/block actions."""
    if callback.from_user is None:
        await callback.answer("Unable to resolve user.", show_alert=True)
        return

    parsed = _parse_callback_data(callback.data)
    if parsed is None:
        await callback.answer("Invalid invite action.", show_alert=True)
        return

    action, invite_id = parsed
    invite = _get_invite_by_id(db, invite_id)
    if invite is None:
        await callback.answer("Invite no longer exists.", show_alert=True)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
        return

    invite_status = str(invite.get("status", "")).lower()
    if invite_status not in {"pending", "accepted", "declined", "revoked", "expired"}:
        await callback.answer("Invite state is invalid.", show_alert=True)
        return

    if invite_status in {"declined", "revoked", "expired"}:
        await callback.answer("This invite is no longer active.", show_alert=True)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
        return

    invitee_user_id_raw = invite.get("invitee_user_id")
    if invitee_user_id_raw is not None:
        try:
            if int(invitee_user_id_raw) != callback.from_user.id:
                await callback.answer("This invite was assigned to another user.", show_alert=True)
                return
        except (TypeError, ValueError):
            pass

    actor_profile = db.get_or_create_profile(
        telegram_user_id=callback.from_user.id,
        username=callback.from_user.username,
        display_name=_display_name_from_callback(callback),
    )

    inviter_id = str(invite.get("inviter", ""))
    invitee_id = str(actor_profile.get("id", ""))
    if not inviter_id or not invitee_id:
        await callback.answer("Invite is invalid.", show_alert=True)
        return

    if inviter_id == invitee_id:
        await callback.answer("You cannot respond to your own invite.", show_alert=True)
        return

    friendship = db.create_or_get_friendship(inviter_id=inviter_id, invitee_id=invitee_id)
    friendship_id = str(friendship.get("id", ""))
    if not friendship_id:
        await callback.answer("Unable to update friendship.", show_alert=True)
        return

    try:
        if action == CALLBACK_ACCEPT:
            db.set_friendship_status(
                friendship_id=friendship_id,
                status="accepted",
                accepted_at=datetime.now(timezone.utc),
            )
            _update_invite(
                db=db,
                invite_id=invite_id,
                payload={
                    "status": "accepted",
                    "used_at": datetime.now(timezone.utc).isoformat(),
                    "invitee_user_id": callback.from_user.id,
                    "invitee_username": callback.from_user.username,
                },
            )
            result_text = "Invite accepted. You're now connected."
        elif action == CALLBACK_DECLINE:
            db.set_friendship_status(friendship_id=friendship_id, status="declined")
            _update_invite(
                db=db,
                invite_id=invite_id,
                payload={
                    "status": "declined",
                    "used_at": None,
                    "invitee_user_id": callback.from_user.id,
                    "invitee_username": callback.from_user.username,
                },
            )
            result_text = "Invite declined."
        elif action == CALLBACK_BLOCK:
            db.set_friendship_status(friendship_id=friendship_id, status="blocked")
            _update_invite(
                db=db,
                invite_id=invite_id,
                payload={
                    "status": "revoked",
                    "used_at": None,
                    "invitee_user_id": callback.from_user.id,
                    "invitee_username": callback.from_user.username,
                },
            )
            result_text = "Invite blocked. You will not be connected."
        else:
            await callback.answer("Unsupported action.", show_alert=True)
            return
    except Exception:
        logger.exception("Failed to process invite callback")
        await callback.answer("Failed to update invite state.", show_alert=True)
        return

    await callback.answer("Updated")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(result_text)


async def _handle_invite_start(
    message: Message,
    db: Database,
    invite_code: str,
    invitee_profile: dict[str, Any],
) -> None:
    invite = db.get_invite_by_code(invite_code)
    if invite is None:
        await message.answer("Invite not found.")
        return

    invite_status = str(invite.get("status", "")).lower()
    if invite_status != "pending":
        await message.answer("This invite is no longer active.")
        return

    if _invite_is_expired(invite):
        _update_invite(db=db, invite_id=str(invite["id"]), payload={"status": "expired"})
        await message.answer("This invite has expired.")
        return

    inviter_id = str(invite.get("inviter", ""))
    invitee_id = str(invitee_profile.get("id", ""))

    if not inviter_id or not invitee_id:
        await message.answer("Invite data is incomplete.")
        return

    if inviter_id == invitee_id:
        await message.answer("You cannot use your own invite link.")
        return

    _update_invite(
        db=db,
        invite_id=str(invite["id"]),
        payload={
            "invitee_user_id": message.from_user.id if message.from_user else None,
            "invitee_username": message.from_user.username if message.from_user else None,
        },
    )

    friendship = db.create_or_get_friendship(inviter_id=inviter_id, invitee_id=invitee_id)
    friendship_id = str(friendship.get("id", ""))
    status = str(friendship.get("status", "")).lower()

    inviter_name = _profile_label(_get_profile_by_id(db, inviter_id))

    if status == "accepted":
        await message.answer(f"You are already connected with {escape(inviter_name)}.")
        return

    if friendship_id:
        db.set_friendship_status(friendship_id=friendship_id, status="pending")

    await message.answer(
        f"<b>{escape(inviter_name)}</b> invited you to split bills.\n"
        "Choose how to proceed:",
        reply_markup=_build_invite_keyboard(str(invite["id"])),
    )


def _parse_callback_data(data: str | None) -> tuple[str, str] | None:
    if not data:
        return None

    parts = data.split(":", maxsplit=2)
    if len(parts) != 3:
        return None

    prefix, action, invite_id = parts
    if prefix != CALLBACK_PREFIX:
        return None
    if action not in {CALLBACK_ACCEPT, CALLBACK_DECLINE, CALLBACK_BLOCK}:
        return None

    try:
        UUID(invite_id)
    except ValueError:
        return None

    return action, invite_id


def _build_invite_keyboard(invite_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Accept",
                    callback_data=f"{CALLBACK_PREFIX}:{CALLBACK_ACCEPT}:{invite_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Decline",
                    callback_data=f"{CALLBACK_PREFIX}:{CALLBACK_DECLINE}:{invite_id}",
                ),
                InlineKeyboardButton(
                    text="🚫 Block",
                    callback_data=f"{CALLBACK_PREFIX}:{CALLBACK_BLOCK}:{invite_id}",
                ),
            ]
        ]
    )


def _update_invite(db: Database, invite_id: str, payload: dict[str, Any]) -> None:
    db.client.table("invites").update(payload).eq("id", invite_id).execute()


def _get_invite_by_id(db: Database, invite_id: str) -> dict[str, Any] | None:
    response = db.client.table("invites").select("*").eq("id", invite_id).limit(1).execute()
    data = response.data if isinstance(response.data, list) else []
    return data[0] if data else None


def _get_profile_by_id(db: Database, profile_id: str) -> dict[str, Any] | None:
    response = db.client.table("profiles").select("*").eq("id", profile_id).limit(1).execute()
    data = response.data if isinstance(response.data, list) else []
    return data[0] if data else None


def _profile_label(profile: dict[str, Any] | None) -> str:
    if not profile:
        return "Someone"

    display_name = str(profile.get("display_name", "")).strip()
    if display_name:
        return display_name

    username = str(profile.get("telegram_username", "")).strip()
    if username:
        return f"@{username}"

    telegram_user_id = profile.get("telegram_user_id")
    return f"user {telegram_user_id}" if telegram_user_id else "Someone"


def _display_name(message: Message) -> str | None:
    if message.from_user is None:
        return None

    first_name = (message.from_user.first_name or "").strip()
    last_name = (message.from_user.last_name or "").strip()
    combined = f"{first_name} {last_name}".strip()
    return combined or None


def _display_name_from_callback(callback: CallbackQuery) -> str | None:
    first_name = (callback.from_user.first_name or "").strip()
    last_name = (callback.from_user.last_name or "").strip()
    combined = f"{first_name} {last_name}".strip()
    return combined or None


def _invite_is_expired(invite: dict[str, Any]) -> bool:
    expires_at = invite.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at:
        return False

    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False

    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    return expires < datetime.now(timezone.utc)
