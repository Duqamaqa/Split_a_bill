from __future__ import annotations

import asyncio
import logging

from .application import create_bot, get_dispatcher, set_bot_commands
from .config import get_settings
from .db import Database

logger = logging.getLogger(__name__)


async def main() -> None:
    """Application entrypoint for long-polling mode."""
    settings = get_settings()
    database = Database(settings)
    database.assert_ready()

    bot = create_bot(settings)
    dispatcher = get_dispatcher()

    await set_bot_commands(bot)

    logger.info("Bot starting in long polling mode")
    try:
        await dispatcher.start_polling(
            bot,
            close_bot_session=False,
            db=database,
            settings=settings,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        database.close()


if __name__ == "__main__":
    asyncio.run(main())
