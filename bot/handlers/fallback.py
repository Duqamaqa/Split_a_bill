from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

router = Router(name="fallback")


@router.message(F.text.startswith("/"))
async def handle_unknown_command(message: Message) -> None:
    if message.chat.type != ChatType.PRIVATE:
        return
    await message.answer(
        "I don't recognize this command.\n"
        "Use /help to see clear examples."
    )


@router.message(F.text)
async def handle_plain_text(message: Message) -> None:
    if message.chat.type != ChatType.PRIVATE:
        return
    await message.answer(
        "Tip: use commands to work with me.\n"
        "Start with /help, /invite @friend, or /out /in /balance (friend picker)."
    )
