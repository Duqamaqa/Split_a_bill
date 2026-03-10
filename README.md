# Split a Bill Telegram Bot (Simple Mode)

Minimal Telegram debt bot with exactly 3 user actions:
- `In`
- `Balance`
- `Close`

Tech stack:
- aiogram v3 (long polling)
- PostgreSQL (direct via psycopg)
- pydantic-settings

## How it works

1. User taps `In`.
2. Bot asks for amount (`120` or `120 USD`).
3. Bot creates a deep link like `https://t.me/<BOT_USERNAME>?start=pay_<CODE>`.
4. User forwards that link to the person who gave the money.
5. That person opens the link and taps `Approve`.
6. Bot writes a confirmed transaction to PostgreSQL.

`Balance` shows only non-zero debts.

`Close` shows people with open debts as buttons. Tapping a person sets your mutual balances to `0`.

## Requirements

- Python 3.11+
- `pip`
- PostgreSQL 14+

## Setup

1. Open terminal in project:

```bash
cd /Users/coconut/Documents/projects/Split_a_bill
```

2. Create virtual environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Create env file:

```bash
cp .env.example .env
```

5. Fill `.env`:

```env
BOT_TOKEN=<telegram-bot-token>
BOT_USERNAME=<bot_username_without_@>
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/split_bill
DEFAULT_CURRENCY=ILS
```

6. Run SQL in PostgreSQL:

- `postgres/schema.sql`
- If you already have old tables and only need request-links feature:
  - `postgres/migrations/20260306_payment_requests.sql`

7. Run bot:

```bash
python -m bot.main
```

## Database note

This version requires table `payment_requests` (included in `postgres/schema.sql`).
