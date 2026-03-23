from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from ..db import Database

logger = logging.getLogger(__name__)


class UpdateIdempotencyMiddleware(BaseMiddleware):
    """Skip duplicate Telegram updates based on `update_id` persistence."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        db = data.get("db")
        if not isinstance(db, Database):
            return await handler(event, data)

        update_id = int(event.update_id)

        try:
            already_processed = db.is_update_processed(update_id)
        except Exception:
            logger.exception("Failed idempotency check for update_id=%s", update_id)
            return await handler(event, data)

        if already_processed:
            logger.info("Skipping duplicate update_id=%s", update_id)
            return None

        result = await handler(event, data)

        try:
            marked = db.mark_update_processed(update_id)
            if not marked:
                logger.info("update_id=%s was marked concurrently", update_id)
        except Exception:
            # Processing succeeded, so do not fail the update because post-marking failed.
            logger.exception("Failed to persist processed update_id=%s", update_id)

        return result
