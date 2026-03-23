from __future__ import annotations

import asyncio

from .application import create_bot, get_dispatcher, set_bot_commands
from .config import get_settings


async def main() -> None:
    settings = get_settings()
    webhook_url = settings.telegram_webhook_url

    bot = create_bot(settings)
    dispatcher = get_dispatcher()
    try:
        await set_bot_commands(bot)
        await bot.set_webhook(
            webhook_url,
            allowed_updates=dispatcher.resolve_used_update_types(),
            secret_token=settings.webhook_secret,
            drop_pending_updates=False,
        )
        webhook_info = await bot.get_webhook_info()
    finally:
        await bot.session.close()

    print(f"Webhook set to: {webhook_info.url}")
    print(f"Pending updates: {webhook_info.pending_update_count}")
    print(f"Last error: {webhook_info.last_error_message or 'none'}")


if __name__ == "__main__":
    asyncio.run(main())
