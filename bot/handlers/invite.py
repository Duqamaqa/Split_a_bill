from __future__ import annotations

import logging
from datetime import datetime, timezone
from html import escape
from typing import Any

from aiogram import Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..config import Settings
from ..db import Database

router = Router(name="invite")
logger = logging.getLogger(__name__)

INVITE_PREFIX = "inv_"
CALLBACK_PREFIX = "invite"
CALLBACK_ACCEPT = "accept"
CALLBACK_DECLINE = "decline"
CALLBACK_BLOCK = "block"


@router.message(Command("invite"))
async def handle_invite(
    message: Message,
    command: CommandObject,
    settings: Settings,
    db: Database,
) -> None:
    """Create an invite and try to deliver it via DM or deep-link fallback."""
    if message.from_user is None:
        await message.answer("Unable to resolve user context for this command.")
        return

    invitee_username = _extract_invitee_username(command.args)
    if invitee_username is None:
        await message.answer("Usage: /invite @friend\nExample: /invite @friend")
        return

    inviter_profile = db.get_or_create_profile(
        telegram_user_id=message.from_user.id,
        username=message.from_user.username,
        display_name=_display_name(message),
    )

    inviter_username = str(inviter_profile.get("telegram_username", "")).strip().lower()
    if inviter_username and inviter_username == invitee_username.lower():
        await message.answer("You cannot invite yourself.")
        return

    existing_invite = _find_existing_pending_invite(
        db=db,
        inviter_id=str(inviter_profile["id"]),
        invitee_username=invitee_username,
    )
    if existing_invite and not _is_invite_expired(existing_invite):
        invite = existing_invite
        await message.answer(
            f"Invite already exists for @{invitee_username}. Reusing the existing invite."
        )
    else:
        invite = db.create_invite(inviter_id=str(inviter_profile["id"]), invitee_username=invitee_username)
    invite_id = str(invite.get("id", ""))
    invite_code = str(invite.get("code", ""))
    if not invite_id or not invite_code:
        await message.answer("Failed to create invite.")
        return

    dm_sent = False
    target_profile = db.get_profile_by_username(invitee_username)
    if target_profile and target_profile.get("telegram_user_id"):
        dm_sent = await _try_dm_invitee(
            message=message,
            inviter_profile=inviter_profile,
            invite_id=invite_id,
            invitee_tg_id=int(target_profile["telegram_user_id"]),
        )

    if dm_sent:
        await message.answer(
            f"Invite sent to @{invitee_username}.\n"
            "They can accept it from the bot message."
        )
        return

    deep_link = await _build_deep_link(message=message, settings=settings, invite_code=invite_code)
    if deep_link is None:
        await message.answer(
            "Invite created, but I cannot build a deep-link URL. "
            "Set BOT_USERNAME in environment."
        )
        return

    await message.answer(
        f"I couldn't DM @{invitee_username} due to Telegram restrictions.\n"
        f"Send this link to them:\n{deep_link}"
    )


async def _try_dm_invitee(
    message: Message,
    inviter_profile: dict[str, Any],
    invite_id: str,
    invitee_tg_id: int,
) -> bool:
    """Try to DM the invitee with action buttons."""
    inviter_name = _profile_label(inviter_profile)
    text = (
        f"<b>{escape(inviter_name)}</b> invited you to split bills.\n"
        "Choose how to proceed:"
    )

    try:
        await message.bot.send_message(
            chat_id=invitee_tg_id,
            text=text,
            reply_markup=_build_invite_keyboard(invite_id),
        )
    except TelegramForbiddenError:
        logger.info("Invitee blocked bot or has not started it (telegram_user_id=%s)", invitee_tg_id)
        return False
    except Exception:
        logger.info("Unable to DM invitee telegram_user_id=%s", invitee_tg_id, exc_info=True)
        return False

    return True


async def _build_deep_link(message: Message, settings: Settings, invite_code: str) -> str | None:
    bot_username = settings.BOT_USERNAME
    if not bot_username:
        me = await message.bot.get_me()
        bot_username = me.username

    if not bot_username:
        return None

    clean_username = bot_username.strip().lstrip("@")
    if not clean_username:
        return None

    return f"https://t.me/{clean_username}?start={INVITE_PREFIX}{invite_code}"


def _build_invite_keyboard(invite_id: str):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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


def _find_existing_pending_invite(
    *,
    db: Database,
    inviter_id: str,
    invitee_username: str,
) -> dict[str, Any] | None:
    response = (
        db.client.table("invites")
        .select("*")
        .eq("inviter", inviter_id)
        .ilike("invitee_username", invitee_username)
        .eq("status", "pending")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = response.data if isinstance(response.data, list) else []
    return rows[0] if rows else None


def _is_invite_expired(invite: dict[str, Any]) -> bool:
    expires_at = invite.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at.strip():
        return False

    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False

    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    return expiry < datetime.now(timezone.utc)


def _extract_invitee_username(raw_args: str | None) -> str | None:
    if not raw_args or not raw_args.strip():
        return None

    token = raw_args.strip().split()[0].strip()
    if not token:
        return None

    username = token.lstrip("@").strip()
    return username if username else None


def _display_name(message: Message) -> str | None:
    if message.from_user is None:
        return None

    first_name = (message.from_user.first_name or "").strip()
    last_name = (message.from_user.last_name or "").strip()
    combined = f"{first_name} {last_name}".strip()
    return combined or None


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
