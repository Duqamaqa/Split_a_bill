from __future__ import annotations

import secrets
from typing import Any

from aiogram.types import Update
from fastapi import APIRouter, FastAPI, Header, HTTPException, status
from pydantic import ValidationError

from .application import create_bot, get_dispatcher
from .config import Settings, get_settings
from .db import Database

dispatcher = get_dispatcher()


def is_valid_telegram_secret(settings: Settings, header_value: str | None) -> bool:
    secret = settings.webhook_secret
    if secret is None:
        return True
    if header_value is None:
        return False
    return secrets.compare_digest(header_value, secret)


def create_fastapi_app(*, route_prefix: str = "", include_root: bool = False) -> FastAPI:
    app = FastAPI(title="Split a Bill Telegram Bot")
    router = APIRouter(prefix=route_prefix)

    if include_root:
        @app.get("/")
        async def root() -> dict[str, Any]:
            return {"ok": True, "service": "split-a-bill-bot"}

    @router.get("/health")
    async def healthcheck() -> dict[str, Any]:
        settings = get_settings()
        database = Database(settings)
        try:
            database.assert_ready()
        finally:
            database.close()

        return {"ok": True}

    @router.post("/telegram")
    async def telegram_webhook(
        payload: dict[str, Any],
        x_telegram_bot_api_secret_token: str | None = Header(
            default=None,
            alias="X-Telegram-Bot-Api-Secret-Token",
        ),
    ) -> dict[str, Any]:
        settings = get_settings()
        if not is_valid_telegram_secret(settings, x_telegram_bot_api_secret_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Telegram webhook secret.",
            )

        bot = create_bot(settings)
        database = Database(settings)
        try:
            update = Update.model_validate(payload, context={"bot": bot})
            await dispatcher.feed_update(
                bot,
                update,
                db=database,
                settings=settings,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid Telegram update payload: {exc}",
            ) from exc
        finally:
            await bot.session.close()
            database.close()

        return {"ok": True}

    app.include_router(router)
    return app
