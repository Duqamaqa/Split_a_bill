# Split a Bill Telegram Bot (aiogram v3 + Supabase)

Telegram debt-sharing bot built with:
- aiogram v3 (long polling)
- supabase-py
- pydantic-settings

## Requirements

- Python 3.11+
- `pip`

## Project Structure

```text
bot/
  __init__.py
  main.py
  config.py
  currency.py
  db.py
  models.py
  logic.py
  handlers/
    __init__.py
    start.py
    invite.py
    ledger.py
    remind.py
    callbacks.py
  middlewares/
    __init__.py
    idempotency.py
  utils/
    __init__.py
    formatting.py
    rate_limit.py
requirements.txt
.env.example
supabase/schema.sql
supabase/migrations/20260305_multi_currency.sql
tests/
README.md
```

## Setup

1. Copy the env template:

```bash
cp .env.example .env
```

2. Fill `.env`:

```env
BOT_TOKEN=<telegram-bot-token>
BOT_USERNAME=<your_bot_username_without_@>
SUPABASE_URL=<https://your-project.supabase.co>
SUPABASE_SERVICE_ROLE_KEY=<your-service-role-key>
DEFAULT_CURRENCY=ILS
REMIND_COOLDOWN_SECONDS=43200
```

3. Run:

```bash
pip install -r requirements.txt
python -m bot.main
```

## Multi-Currency

Supported currencies: `ILS`, `USD`, `EUR`, `RUB`.

Examples:

- `/setcurrency RUB`
- `/out` (friend picker buttons, then send amount)
- `/in` (friend picker buttons, then send amount)
- `/balance` (friend picker buttons)
- `/history` (friend picker buttons)
- `/remind` (friend picker buttons)
- `/out @friend 100`
- `/out @friend 100 usd`
- `/out @friend 100 USD dinner`
- `/out` is recorded immediately (friend receives info message, no confirm/reject)
- `/balance @friend`
- `/balance @friend RUB`
- `/remind @friend`
- `/remind @friend EUR`

## Sanity Checklist

- Create a bot token in BotFather and put it in `BOT_TOKEN`.
- Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.
- Run the SQL schema from `supabase/schema.sql` in Supabase SQL Editor.
- Run migration SQL: `supabase/migrations/20260305_multi_currency.sql`.
- Set `BOT_USERNAME` (without `@`) to match your bot.
- Test invite acceptance via deep-link (`/invite @friend`, then open `https://t.me/<BOT_USERNAME>?start=inv_<code>`).
- Test `/setcurrency RUB`, then `/out @friend 100` (should use RUB by default).
- Test `/out` confirmation flow (counterparty gets confirm/reject buttons).
- Test `/friends` balance output.
- Test `/remind` cooldown per currency (second reminder in the same currency should be blocked).

## Notes

- `SUPABASE_SERVICE_ROLE_KEY` is sensitive and must stay server-side.
- Run the bot in a private backend environment; never expose env values in client code.
