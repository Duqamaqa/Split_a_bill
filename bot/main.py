from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .config import get_settings
from .db import Database
from .handlers import include_routers
from .middlewares.idempotency import UpdateIdempotencyMiddleware

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """Configure global logging for the bot process."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def set_bot_commands(bot: Bot) -> None:
    """Register the minimal command menu shown in Telegram clients."""
    commands = [BotCommand(command="start", description="Open main menu")]
    await bot.set_my_commands(commands)


async def main() -> None:
    """Application entrypoint for long-polling mode."""
    configure_logging()

    settings = get_settings()
    database = Database(settings)
    database.assert_ready()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dispatcher = Dispatcher()
    dispatcher["settings"] = settings
    dispatcher["db"] = database
    dispatcher.update.outer_middleware(UpdateIdempotencyMiddleware(database))
    include_routers(dispatcher)

    await set_bot_commands(bot)

    logger.info("Bot starting in long polling mode")
    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        database.close()


if __name__ == "__main__":
    asyncio.run(main())
