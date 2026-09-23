# Deployment guide

## Supported application shape

The repository contains a FastAPI webhook entry point at `api/index.py`, plus a polling entry point at `bot/main.py`. This document explains the checked-in configuration; it does not promise a running public deployment.

## Environment

| Variable | Purpose |
| --- | --- |
| `BOT_TOKEN` | Telegram bot credential |
| `BOT_USERNAME` | Bot username, without `@` |
| `DATABASE_URL` | PostgreSQL connection string; `POSTGRES_URL` is accepted as a fallback |
| `DEFAULT_CURRENCY` | Three-letter default currency; defaults to `ILS` |
| `WEBHOOK_SECRET` | Secret for the Telegram webhook header; configure it for hosted use |
| `PUBLIC_BASE_URL` | Base URL used when registering the webhook |

For a development database, a connection string might look like `postgresql://postgres:postgres@localhost:5432/split_bill`. Use your own credentials and keep them in local environment configuration.

Create the tables from [postgres/schema.sql](../postgres/schema.sql) before starting the application. The version expects `payment_requests` and `processed_updates` to be present.

## Vercel-shaped deployment

The Python entry point exposes:

- `POST /api/telegram` for Telegram updates.
- `GET /api/health` for a database-backed health check.

Set the environment values in the hosting service. When the endpoint and database are ready, register the webhook:

```bash
python -m bot.setup_webhook
```

That command changes your Telegram bot's webhook registration to `PUBLIC_BASE_URL` plus `/api/telegram`. Run it only for the intended bot and deployment. A healthy root landing page is not required by this API, and an API health check is not a full conversation test.

## Cloudflare Worker scaffold

`wrangler.toml`, `pyproject.toml`, and `src/entry.py` provide a Worker scaffold. **It is not a working deployment path for the current direct `psycopg` database layer.** The application raises an explanatory runtime error when that driver is unavailable.

Running `pywrangler deploy` does not resolve that mismatch. A compatible container runtime or a redesigned database access boundary would be additional engineering work; neither is implemented by this documentation update.

## Before serving real users

Verify authentication, schema state, retry/concurrency behavior, backups, logs, and end-to-end Telegram flows in a dedicated environment. The checked-in unit tests cover a narrower scope; see [VALIDATION.md](VALIDATION.md).
