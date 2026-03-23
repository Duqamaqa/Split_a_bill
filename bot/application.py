from __future__ import annotations

import logging
from functools import lru_cache

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .config import Settings
from .handlers import include_routers
from .middlewares.idempotency import UpdateIdempotencyMiddleware

_LOGGING_CONFIGURED = False


def configure_logging() -> None:
    """Configure process-wide logging once."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    _LOGGING_CONFIGURED = True


def create_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def set_bot_commands(bot: Bot) -> None:
    """Register the minimal command menu shown in Telegram clients."""
    commands = [BotCommand(command="start", description="Open main menu")]
    await bot.set_my_commands(commands)


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(UpdateIdempotencyMiddleware())
    include_routers(dispatcher)
    return dispatcher


@lru_cache(maxsize=1)
def get_dispatcher() -> Dispatcher:
    configure_logging()
    return create_dispatcher()
