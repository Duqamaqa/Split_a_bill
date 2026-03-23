from bot.webhook_app import create_fastapi_app

app = create_fastapi_app(route_prefix="/api", include_root=True)
