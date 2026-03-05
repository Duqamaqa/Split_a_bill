from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

Direction = Literal["in", "out"]


class ReminderDecision(StrEnum):
    CONFIRM = "remind:confirm"
    REJECT = "remind:reject"


@dataclass(slots=True, frozen=True)
class LedgerEntry:
    user_id: int
    chat_id: int
    amount: Decimal
    currency: str
    direction: Direction
    note: str = ""
    id: int | None = None
    created_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class BalanceSnapshot:
    currency: str
    total_in: Decimal
    total_out: Decimal

    @property
    def balance(self) -> Decimal:
        return self.total_in - self.total_out


@dataclass(slots=True, frozen=True)
class FriendRecord:
    user_id: int
    chat_id: int
    friend_username: str
