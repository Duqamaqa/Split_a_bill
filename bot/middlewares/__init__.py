"""Middleware components for aiogram update processing."""

from .idempotency import UpdateIdempotencyMiddleware

__all__ = ["UpdateIdempotencyMiddleware"]
