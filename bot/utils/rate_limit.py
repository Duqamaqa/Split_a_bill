from __future__ import annotations

import asyncio
import time


class RateLimiter:
    def __init__(self, cooldown_seconds: int) -> None:
        self._cooldown_seconds = max(0, cooldown_seconds)
        self._next_allowed_by_key: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> tuple[bool, int]:
        """
        Returns `(allowed, retry_after_seconds)`.
        """
        now = time.monotonic()

        async with self._lock:
            next_allowed = self._next_allowed_by_key.get(key, 0.0)
            if now < next_allowed:
                return False, int(next_allowed - now)

            self._next_allowed_by_key[key] = now + self._cooldown_seconds
            return True, 0

    async def reset(self, key: str) -> None:
        async with self._lock:
            self._next_allowed_by_key.pop(key, None)
