# Architecture and boundaries

The Telegram interface uses aiogram. Development can poll Telegram directly; the FastAPI entry point receives hosted webhook updates. Handlers call the PostgreSQL data layer and return Telegram responses.

```text
Telegram update → polling or FastAPI → aiogram dispatcher
                                     → handlers → PostgreSQL
                                     → Telegram response
```

## Design choices visible in the code

- **Small user-facing action set.** `In`, `Balance`, and `Close` hide the request/approval/database steps behind a compact conversation.
- **Confirmation links.** A proposed debt is shared before the other person approves it. The link code and database state support the workflow.
- **Decimal money values.** The data layer uses Python Decimal arithmetic rather than relying on binary floating-point values for monetary amounts.
- **One database layer.** Direct `psycopg` access keeps SQL behavior inspectable but ties the application to a runtime that supports that client and network access.
- **Two delivery modes.** Polling is useful locally; hosted webhooks require endpoint and secret configuration.

## Reliability limits

The middleware first checks `processed_updates`, invokes the handler, and only then marks the update processed. Concurrent requests can pass the first check before either request records completion. Database-level rules can protect particular operations, but the middleware alone is not an exactly-once guarantee. An idempotency lookup error also allows processing to continue.

The existing tests focus on parsing and configuration. Transaction behavior, retries, concurrent approvals, balance closure, and recovery need dedicated verification before production use.

## Security and deployment limits

The webhook compares the configured secret with Telegram's header. With no configured secret, the current implementation accepts the request. Set `WEBHOOK_SECRET` and register it with Telegram; do not assume the default is authenticated.

Approval links and debt records should be treated as sensitive application data. Do not publish real conversations or records as demo material. This document does not claim a completed security audit.
