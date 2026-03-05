from __future__ import annotations

import logging
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping
from uuid import UUID

from supabase import Client, create_client

from .config import Settings
from .currency import normalize_currency_code
from .models import FriendRecord, LedgerEntry

try:
    from postgrest.exceptions import APIError
except Exception:  # pragma: no cover - fallback for import compatibility
    APIError = Exception  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_INVITE_CODE_ALPHABET = string.ascii_uppercase + string.digits
_INVITE_CODE_LENGTH = 10
_DEFAULT_INVITE_TTL = timedelta(days=7)
_TWO_DP = Decimal("0.01")
_ZERO = Decimal("0")


class Database:
    def __init__(self, settings: Settings) -> None:
        self._client: Client = create_client(
            settings.SUPABASE_URL,
            settings.supabase_service_role_key,
        )
        self._processed_updates_missing_logged = False

    @property
    def client(self) -> Client:
        return self._client

    def assert_ready(self) -> None:
        """Fail fast when Supabase credentials/schema are not usable."""
        try:
            self._client.table("profiles").select("id").limit(1).execute()
        except APIError as exc:  # type: ignore[misc]
            message = _extract_api_error_message(exc)
            combined_error = f"{message} | {exc}".lower()
            if "invalid api key" in combined_error:
                raise RuntimeError(
                    "Supabase rejected SUPABASE_SERVICE_ROLE_KEY for SUPABASE_URL. "
                    "Use the service_role key from the same Supabase project "
                    "(Dashboard -> Settings -> API) and restart the bot."
                ) from exc
            raise RuntimeError(
                f"Supabase connectivity check failed: {message}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Supabase connectivity check failed: {exc}") from exc

    # ---------- Profiles ----------
    def get_or_create_profile(
        self,
        telegram_user_id: int,
        username: str | None,
        display_name: str | None,
    ) -> dict[str, Any]:
        payload = {
            "telegram_user_id": int(telegram_user_id),
            "telegram_username": _normalize_username(username),
            "display_name": _normalize_text(display_name),
        }

        self._client.table("profiles").upsert(
            payload,
            on_conflict="telegram_user_id",
        ).execute()

        profile = self._get_profile_by_telegram_user_id(int(telegram_user_id))
        if profile is None:
            raise RuntimeError("Failed to create or fetch profile")
        return profile

    def get_profile_by_username(self, username: str | None) -> dict[str, Any] | None:
        normalized = _normalize_username(username)
        if normalized is None:
            return None

        exact = (
            self._client.table("profiles")
            .select("*")
            .eq("telegram_username", normalized)
            .limit(1)
            .execute()
        )
        row = _first_row(exact.data)
        if row is not None:
            return row

        # Best-effort case-insensitive lookup when exact match is unknown.
        insensitive = (
            self._client.table("profiles")
            .select("*")
            .ilike("telegram_username", normalized)
            .limit(1)
            .execute()
        )
        return _first_row(insensitive.data)

    def set_profile_default_currency(
        self,
        profile_id: str,
        currency: str,
    ) -> dict[str, Any] | None:
        response = (
            self._client.table("profiles")
            .update({"default_currency": _normalize_currency(currency)})
            .eq("id", _normalize_uuid(profile_id))
            .execute()
        )
        return _first_row(response.data)

    # ---------- Invites ----------
    def create_invite(self, inviter_id: str, invitee_username: str | None) -> dict[str, Any]:
        inviter_id = _normalize_uuid(inviter_id)
        normalized_username = _normalize_username(invitee_username)
        expires_at = (datetime.now(timezone.utc) + _DEFAULT_INVITE_TTL).isoformat()

        for _ in range(8):
            code = _generate_invite_code()
            payload = {
                "code": code,
                "inviter": inviter_id,
                "invitee_username": normalized_username,
                "status": "pending",
                "expires_at": expires_at,
            }
            try:
                response = self._client.table("invites").insert(payload).execute()
            except APIError as exc:  # type: ignore[misc]
                if _is_unique_violation(exc):
                    continue
                raise

            row = _first_row(response.data)
            if row is not None:
                return row

        raise RuntimeError("Unable to create a unique invite code")

    def get_invite_by_code(self, code: str) -> dict[str, Any] | None:
        normalized_code = code.strip()
        if not normalized_code:
            return None

        response = (
            self._client.table("invites")
            .select("*")
            .eq("code", normalized_code)
            .limit(1)
            .execute()
        )
        return _first_row(response.data)

    # ---------- Friendships ----------
    def create_or_get_friendship(self, inviter_id: str, invitee_id: str) -> dict[str, Any]:
        inviter_id = _normalize_uuid(inviter_id)
        invitee_id = _normalize_uuid(invitee_id)

        if inviter_id == invitee_id:
            raise ValueError("Cannot create friendship with the same profile")

        user_low, user_high = _canonical_pair(inviter_id, invitee_id)

        existing = self._get_friendship_by_pair(user_low, user_high)
        if existing is not None:
            return existing

        payload = {
            "user_low": user_low,
            "user_high": user_high,
            "invited_by": inviter_id,
            "status": "pending",
            "accepted_at": None,
        }

        try:
            response = self._client.table("friendships").insert(payload).execute()
            row = _first_row(response.data)
            if row is not None:
                return row
        except APIError as exc:  # type: ignore[misc]
            if not _is_unique_violation(exc):
                raise

        # Race-safe fallback.
        existing = self._get_friendship_by_pair(user_low, user_high)
        if existing is None:
            raise RuntimeError("Failed to create or fetch friendship")
        return existing

    def set_friendship_status(
        self,
        friendship_id: str,
        status: str,
        accepted_at: datetime | str | None = None,
    ) -> dict[str, Any] | None:
        status = status.strip().lower()
        allowed = {"pending", "accepted", "declined", "blocked"}
        if status not in allowed:
            raise ValueError(f"Unsupported friendship status: {status}")

        payload: dict[str, Any] = {"status": status}
        if status == "accepted":
            payload["accepted_at"] = _to_iso_datetime(accepted_at)
        else:
            payload["accepted_at"] = None

        response = (
            self._client.table("friendships")
            .update(payload)
            .eq("id", _normalize_uuid(friendship_id))
            .execute()
        )
        return _first_row(response.data)

    def list_friends(self, user_id: str) -> list[dict[str, Any]]:
        normalized_user_id = _normalize_uuid(user_id)

        friendships = self._list_friendships_for_user(normalized_user_id)
        if not friendships:
            return []

        friend_ids: set[str] = set()
        friendship_ids: list[str] = []
        for friendship in friendships:
            friendship_id = str(friendship.get("id", ""))
            if friendship_id:
                friendship_ids.append(friendship_id)

            user_low = str(friendship.get("user_low", ""))
            user_high = str(friendship.get("user_high", ""))
            friend_id = user_high if user_low == normalized_user_id else user_low
            if friend_id:
                friend_ids.add(friend_id)

        profiles_by_id = self._get_profiles_by_id(friend_ids)
        balances_by_friendship = self._get_balances_by_friendship(friendship_ids)

        results: list[dict[str, Any]] = []
        for friendship in friendships:
            friendship_id = str(friendship.get("id", ""))
            user_low = str(friendship.get("user_low", ""))
            user_high = str(friendship.get("user_high", ""))
            friend_id = user_high if user_low == normalized_user_id else user_low
            friend_profile = profiles_by_id.get(friend_id)
            balance_rows = balances_by_friendship.get(friendship_id, [])

            per_currency: list[dict[str, Any]] = []
            for balance_row in balance_rows:
                raw_net = _to_decimal(balance_row.get("net_amount"))
                # Positive perspective means friend owes current user.
                perspective = raw_net if user_low == normalized_user_id else -raw_net

                per_currency.append(
                    {
                        "currency": str(balance_row.get("currency", "")).upper(),
                        "net_amount": _decimal_to_str(raw_net),
                        "they_owe_you": _decimal_to_str(max(perspective, _ZERO)),
                        "you_owe": _decimal_to_str(max(-perspective, _ZERO)),
                    }
                )

            results.append(
                {
                    "friendship_id": friendship_id,
                    "friend_profile": friend_profile,
                    "balance": per_currency,
                }
            )

        return results

    # ---------- Transactions ----------
    def create_pending_transaction(
        self,
        friendship_id: str,
        created_by: str,
        direction: str,
        amount: Decimal | str | int | float,
        currency: str,
        note: str | None,
    ) -> dict[str, Any]:
        friendship_id = _normalize_uuid(friendship_id)
        created_by = _normalize_uuid(created_by)

        direction = direction.strip().lower()
        if direction not in {"in", "out"}:
            raise ValueError("Direction must be 'in' or 'out'")

        normalized_amount = _normalize_amount(amount)
        normalized_currency = _normalize_currency(currency)

        friendship = self._get_friendship_by_id(friendship_id)
        if friendship is None:
            raise ValueError("Friendship does not exist")

        members = {
            str(friendship.get("user_low", "")),
            str(friendship.get("user_high", "")),
        }
        if created_by not in members:
            raise ValueError("Transaction creator must belong to the friendship")

        payload = {
            "friendship_id": friendship_id,
            "created_by": created_by,
            "direction": direction,
            "amount": _decimal_to_str(normalized_amount),
            "currency": normalized_currency,
            "note": _normalize_text(note),
            "status": "pending",
        }

        response = self._client.table("transactions").insert(payload).execute()
        row = _first_row(response.data)
        if row is None:
            raise RuntimeError("Failed to create pending transaction")
        return row

    def create_confirmed_transaction(
        self,
        friendship_id: str,
        created_by: str,
        direction: str,
        amount: Decimal | str | int | float,
        currency: str,
        note: str | None,
    ) -> dict[str, Any]:
        """
        Create an immediately confirmed transaction and apply its balance effect.
        """
        friendship_id = _normalize_uuid(friendship_id)
        created_by = _normalize_uuid(created_by)

        direction = direction.strip().lower()
        if direction not in {"in", "out"}:
            raise ValueError("Direction must be 'in' or 'out'")

        normalized_amount = _normalize_amount(amount)
        normalized_currency = _normalize_currency(currency)

        friendship = self._get_friendship_by_id(friendship_id)
        if friendship is None:
            raise ValueError("Friendship does not exist")

        members = {
            str(friendship.get("user_low", "")),
            str(friendship.get("user_high", "")),
        }
        if created_by not in members:
            raise ValueError("Transaction creator must belong to the friendship")

        payload = {
            "friendship_id": friendship_id,
            "created_by": created_by,
            "direction": direction,
            "amount": _decimal_to_str(normalized_amount),
            "currency": normalized_currency,
            "note": _normalize_text(note),
            "status": "confirmed",
            "confirmed_by": created_by,
            "confirmed_at": _now_utc_iso(),
            "rejected_at": None,
            "reversed_at": None,
            "reverses_transaction_id": None,
        }

        response = self._client.table("transactions").insert(payload).execute()
        row = _first_row(response.data)
        if row is None:
            raise RuntimeError("Failed to create confirmed transaction")

        delta = _transaction_effect_on_net(tx=row, friendship=friendship)
        balance_row = self._apply_balance_delta(
            friendship_id=str(row["friendship_id"]),
            currency=str(row["currency"]),
            delta=delta,
        )
        if balance_row is not None:
            row["net_amount_after"] = balance_row.get("net_amount")
        return row

    def confirm_transaction(self, tx_id: str, confirmer_user_id: str) -> dict[str, Any] | None:
        tx_id = _normalize_uuid(tx_id)
        confirmer_user_id = _normalize_uuid(confirmer_user_id)

        tx = self._get_transaction_by_id(tx_id)
        if tx is None:
            return None

        current_status = str(tx.get("status", "")).lower()
        if current_status == "confirmed":
            # Safe retry path when previous attempt confirmed tx but failed before balance write.
            self._recompute_and_upsert_balance(str(tx["friendship_id"]), str(tx["currency"]))
            return tx

        if current_status != "pending":
            return tx

        friendship = self._get_friendship_by_id(str(tx["friendship_id"]))
        if friendship is None:
            raise ValueError("Friendship for transaction was not found")

        _validate_tx_reviewer(
            friendship=friendship,
            tx_created_by=str(tx.get("created_by", "")),
            reviewer_id=confirmer_user_id,
        )

        payload = {
            "status": "confirmed",
            "confirmed_by": confirmer_user_id,
            "confirmed_at": _now_utc_iso(),
            "rejected_at": None,
            "reversed_at": None,
            "reverses_transaction_id": None,
        }

        # Compare-and-set: only transition pending -> confirmed.
        response = (
            self._client.table("transactions")
            .update(payload)
            .eq("id", tx_id)
            .eq("status", "pending")
            .execute()
        )

        updated = _first_row(response.data)
        if updated is None:
            latest = self._get_transaction_by_id(tx_id)
            if latest is not None and str(latest.get("status", "")).lower() == "confirmed":
                self._recompute_and_upsert_balance(
                    str(latest["friendship_id"]),
                    str(latest["currency"]),
                )
            return latest

        delta = _transaction_effect_on_net(tx=updated, friendship=friendship)
        balance_row = self._apply_balance_delta(
            friendship_id=str(updated["friendship_id"]),
            currency=str(updated["currency"]),
            delta=delta,
        )
        if balance_row is not None:
            updated["net_amount_after"] = balance_row.get("net_amount")
        return updated

    def reject_transaction(self, tx_id: str, confirmer_user_id: str) -> dict[str, Any] | None:
        tx_id = _normalize_uuid(tx_id)
        confirmer_user_id = _normalize_uuid(confirmer_user_id)

        tx = self._get_transaction_by_id(tx_id)
        if tx is None:
            return None

        current_status = str(tx.get("status", "")).lower()
        if current_status == "rejected":
            return tx

        if current_status != "pending":
            return tx

        friendship = self._get_friendship_by_id(str(tx["friendship_id"]))
        if friendship is None:
            raise ValueError("Friendship for transaction was not found")

        _validate_tx_reviewer(
            friendship=friendship,
            tx_created_by=str(tx.get("created_by", "")),
            reviewer_id=confirmer_user_id,
        )

        payload = {
            "status": "rejected",
            "confirmed_by": None,
            "confirmed_at": None,
            "rejected_at": _now_utc_iso(),
            "reversed_at": None,
            "reverses_transaction_id": None,
        }

        response = (
            self._client.table("transactions")
            .update(payload)
            .eq("id", tx_id)
            .eq("status", "pending")
            .execute()
        )
        updated = _first_row(response.data)
        if updated is not None:
            return updated

        return self._get_transaction_by_id(tx_id)

    def list_transactions(self, friendship_id: str, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        response = (
            self._client.table("transactions")
            .select("*")
            .eq("friendship_id", _normalize_uuid(friendship_id))
            .order("created_at", desc=True)
            .limit(safe_limit)
            .execute()
        )
        return _rows(response.data)

    # ---------- Telegram update idempotency ----------
    def mark_update_processed(self, update_id: int) -> bool:
        normalized_update_id = int(update_id)
        try:
            self._client.table("processed_updates").insert(
                {"update_id": normalized_update_id}
            ).execute()
            return True
        except APIError as exc:  # type: ignore[misc]
            if _is_missing_table_error(exc, table_name="processed_updates"):
                self._warn_missing_processed_updates_once()
                return False
            if _is_unique_violation(exc):
                return False
            raise

    def is_update_processed(self, update_id: int) -> bool:
        normalized_update_id = int(update_id)
        try:
            response = (
                self._client.table("processed_updates")
                .select("update_id")
                .eq("update_id", normalized_update_id)
                .limit(1)
                .execute()
            )
            return _first_row(response.data) is not None
        except APIError as exc:  # type: ignore[misc]
            if _is_missing_table_error(exc, table_name="processed_updates"):
                self._warn_missing_processed_updates_once()
                return False
            raise

    def _warn_missing_processed_updates_once(self) -> None:
        if self._processed_updates_missing_logged:
            return
        self._processed_updates_missing_logged = True
        logger.warning(
            "processed_updates table is missing; update idempotency is disabled. "
            "Run the latest Supabase schema/migration."
        )

    # ---------- Backward-compatible wrappers (legacy handlers) ----------
    def insert_ledger_entry(self, entry: LedgerEntry) -> bool:
        profile = self._get_profile_by_telegram_user_id(entry.user_id)
        if profile is None:
            logger.warning("No profile found for telegram_user_id=%s", entry.user_id)
            return False

        friendship = self._get_any_accepted_friendship(str(profile["id"]))
        if friendship is None:
            logger.warning("No accepted friendship found for profile_id=%s", profile["id"])
            return False

        try:
            self.create_pending_transaction(
                friendship_id=str(friendship["id"]),
                created_by=str(profile["id"]),
                direction=entry.direction,
                amount=entry.amount,
                currency=entry.currency,
                note=entry.note,
            )
        except Exception:
            logger.exception("Failed to insert legacy ledger entry")
            return False

        return True

    def get_ledger_entries(self, *, user_id: int, chat_id: int, limit: int = 50) -> list[LedgerEntry]:
        del chat_id  # chat_id is not part of the normalized relational model.

        profile = self._get_profile_by_telegram_user_id(user_id)
        if profile is None:
            return []

        response = (
            self._client.table("transactions")
            .select("id,amount,currency,direction,note,created_at")
            .eq("created_by", str(profile["id"]))
            .order("created_at", desc=True)
            .limit(max(1, min(int(limit), 200)))
            .execute()
        )

        entries: list[LedgerEntry] = []
        for row in _rows(response.data):
            amount = _to_decimal(row.get("amount"))
            created_at = _parse_datetime(row.get("created_at"))
            direction = str(row.get("direction", "out")).lower()
            if direction not in {"in", "out"}:
                direction = "out"

            entries.append(
                LedgerEntry(
                    user_id=user_id,
                    chat_id=0,
                    amount=amount,
                    currency=str(row.get("currency", "ILS")).upper(),
                    direction=direction,
                    note=str(row.get("note", "")).strip(),
                    id=None,
                    created_at=created_at,
                )
            )

        return entries

    def get_friends(self, *, user_id: int, chat_id: int, limit: int = 20) -> list[FriendRecord]:
        profile = self._get_profile_by_telegram_user_id(user_id)
        if profile is None:
            return []

        items = self.list_friends(str(profile["id"]))
        records: list[FriendRecord] = []
        for item in items[: max(1, limit)]:
            friend_profile = item.get("friend_profile") or {}
            username = str(friend_profile.get("telegram_username", "")).strip()
            if not username:
                continue

            records.append(
                FriendRecord(
                    user_id=user_id,
                    chat_id=chat_id,
                    friend_username=username,
                )
            )

        return records

    # ---------- Internal helpers ----------
    def _get_profile_by_telegram_user_id(self, telegram_user_id: int) -> dict[str, Any] | None:
        response = (
            self._client.table("profiles")
            .select("*")
            .eq("telegram_user_id", int(telegram_user_id))
            .limit(1)
            .execute()
        )
        return _first_row(response.data)

    def _get_friendship_by_pair(self, user_low: str, user_high: str) -> dict[str, Any] | None:
        response = (
            self._client.table("friendships")
            .select("*")
            .eq("user_low", user_low)
            .eq("user_high", user_high)
            .limit(1)
            .execute()
        )
        return _first_row(response.data)

    def _get_friendship_by_id(self, friendship_id: str) -> dict[str, Any] | None:
        response = (
            self._client.table("friendships")
            .select("*")
            .eq("id", _normalize_uuid(friendship_id))
            .limit(1)
            .execute()
        )
        return _first_row(response.data)

    def _get_transaction_by_id(self, tx_id: str) -> dict[str, Any] | None:
        response = (
            self._client.table("transactions")
            .select("*")
            .eq("id", _normalize_uuid(tx_id))
            .limit(1)
            .execute()
        )
        return _first_row(response.data)

    def _list_friendships_for_user(self, user_id: str) -> list[dict[str, Any]]:
        response = (
            self._client.table("friendships")
            .select("*")
            .eq("status", "accepted")
            .or_(f"user_low.eq.{user_id},user_high.eq.{user_id}")
            .execute()
        )
        return _rows(response.data)

    def _get_profiles_by_id(self, profile_ids: set[str]) -> dict[str, dict[str, Any]]:
        if not profile_ids:
            return {}

        response = (
            self._client.table("profiles")
            .select("id,telegram_user_id,telegram_username,display_name,default_currency")
            .in_("id", sorted(profile_ids))
            .execute()
        )

        return {
            str(row.get("id")): row
            for row in _rows(response.data)
            if row.get("id") is not None
        }

    def _get_balances_by_friendship(self, friendship_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not friendship_ids:
            return {}

        response = (
            self._client.table("balances")
            .select("friendship_id,currency,net_amount")
            .in_("friendship_id", friendship_ids)
            .execute()
        )

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in _rows(response.data):
            friendship_id = str(row.get("friendship_id", ""))
            if not friendship_id:
                continue
            grouped.setdefault(friendship_id, []).append(row)

        return grouped

    def _get_any_accepted_friendship(self, user_id: str) -> dict[str, Any] | None:
        response = (
            self._client.table("friendships")
            .select("*")
            .eq("status", "accepted")
            .or_(f"user_low.eq.{user_id},user_high.eq.{user_id}")
            .limit(1)
            .execute()
        )
        return _first_row(response.data)

    def _recompute_and_upsert_balance(self, friendship_id: str, currency: str) -> dict[str, Any]:
        friendship_id = _normalize_uuid(friendship_id)
        normalized_currency = _normalize_currency(currency)

        friendship = self._get_friendship_by_id(friendship_id)
        if friendship is None:
            raise RuntimeError(f"Friendship not found: {friendship_id}")

        response = (
            self._client.table("transactions")
            .select("created_by,direction,amount")
            .eq("friendship_id", friendship_id)
            .eq("status", "confirmed")
            .eq("currency", normalized_currency)
            .execute()
        )

        net = _ZERO
        for tx in _rows(response.data):
            net += _transaction_effect_on_net(tx=tx, friendship=friendship)

        payload = {
            "friendship_id": friendship_id,
            "currency": normalized_currency,
            "net_amount": _decimal_to_str(net),
        }

        upsert_response = (
            self._client.table("balances")
            .upsert(payload, on_conflict="friendship_id,currency")
            .execute()
        )
        row = _first_row(upsert_response.data)
        if row is None:
            raise RuntimeError("Failed to upsert balance")
        return row

    def _apply_balance_delta(
        self,
        friendship_id: str,
        currency: str,
        delta: Decimal | str | int | float,
    ) -> dict[str, Any] | None:
        """
        Apply a delta to balances.net_amount with optimistic compare-and-set retries.
        """
        friendship_id = _normalize_uuid(friendship_id)
        normalized_currency = _normalize_currency(currency)
        normalized_delta = _to_decimal(delta)

        if normalized_delta == _ZERO:
            return self._get_balance_row(friendship_id, normalized_currency)

        for _ in range(8):
            current_row = self._get_balance_row(friendship_id, normalized_currency)
            if current_row is None:
                payload = {
                    "friendship_id": friendship_id,
                    "currency": normalized_currency,
                    "net_amount": _decimal_to_str(normalized_delta),
                }
                try:
                    inserted = self._client.table("balances").insert(payload).execute()
                except APIError as exc:  # type: ignore[misc]
                    if _is_unique_violation(exc):
                        continue
                    raise

                row = _first_row(inserted.data)
                if row is not None:
                    return row
                continue

            current = _to_decimal(current_row.get("net_amount"))
            target = current + normalized_delta
            response = (
                self._client.table("balances")
                .update({"net_amount": _decimal_to_str(target)})
                .eq("friendship_id", friendship_id)
                .eq("currency", normalized_currency)
                .eq("net_amount", _decimal_to_str(current))
                .execute()
            )
            row = _first_row(response.data)
            if row is not None:
                return row

        # Final correctness fallback if heavy contention occurs.
        return self._recompute_and_upsert_balance(friendship_id, normalized_currency)

    def _get_balance_row(self, friendship_id: str, currency: str) -> dict[str, Any] | None:
        response = (
            self._client.table("balances")
            .select("friendship_id,currency,net_amount")
            .eq("friendship_id", _normalize_uuid(friendship_id))
            .eq("currency", _normalize_currency(currency))
            .limit(1)
            .execute()
        )
        return _first_row(response.data)


def _validate_tx_reviewer(
    friendship: Mapping[str, Any],
    tx_created_by: str,
    reviewer_id: str,
) -> None:
    members = {
        str(friendship.get("user_low", "")),
        str(friendship.get("user_high", "")),
    }
    if reviewer_id not in members:
        raise ValueError("Reviewer is not a member of this friendship")
    if reviewer_id == tx_created_by:
        raise ValueError("Creator cannot confirm/reject their own transaction")


def _transaction_effect_on_net(tx: Mapping[str, Any], friendship: Mapping[str, Any]) -> Decimal:
    """
    Returns transaction impact on balances.net_amount.

    balances.net_amount > 0 means user_high owes user_low.
    """
    amount = _to_decimal(tx.get("amount"))
    direction = str(tx.get("direction", "")).lower()
    if direction not in {"in", "out"}:
        raise ValueError(f"Invalid transaction direction: {direction}")

    created_by = str(tx.get("created_by", ""))
    user_low = str(friendship.get("user_low", ""))
    user_high = str(friendship.get("user_high", ""))

    if created_by not in {user_low, user_high}:
        raise ValueError("Transaction creator is not part of friendship")

    if created_by == user_low:
        return amount if direction == "out" else -amount

    # created_by == user_high
    return -amount if direction == "out" else amount


def _rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _first_row(data: Any) -> dict[str, Any] | None:
    rows = _rows(data)
    return rows[0] if rows else None


def _normalize_uuid(value: str | UUID) -> str:
    return str(UUID(str(value)))


def _canonical_pair(user_a: str | UUID, user_b: str | UUID) -> tuple[str, str]:
    left = _normalize_uuid(user_a)
    right = _normalize_uuid(user_b)
    if left <= right:
        return left, right
    return right, left


def _normalize_username(username: str | None) -> str | None:
    if username is None:
        return None
    normalized = username.strip().lstrip("@")
    return normalized if normalized else None


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if normalized else None


def _normalize_currency(currency: str) -> str:
    return normalize_currency_code(currency)


def _normalize_amount(value: Decimal | str | int | float) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid amount") from exc

    if amount <= _ZERO:
        raise ValueError("Amount must be greater than zero")
    return amount


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return _ZERO


def _decimal_to_str(value: Decimal) -> str:
    return str(value.quantize(_TWO_DP, rounding=ROUND_HALF_UP))


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None

    iso_value = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_value)
    except ValueError:
        return None


def _to_iso_datetime(value: datetime | str | None) -> str:
    if value is None:
        return _now_utc_iso()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    parsed = _parse_datetime(value)
    if parsed is None:
        return _now_utc_iso()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_invite_code() -> str:
    return "".join(secrets.choice(_INVITE_CODE_ALPHABET) for _ in range(_INVITE_CODE_LENGTH))


def _extract_api_error_message(exc: Exception) -> str:
    raw_message = getattr(exc, "message", None)
    if isinstance(raw_message, str):
        cleaned = raw_message.strip()
        if cleaned and cleaned.lower() != "json could not be generated":
            return cleaned

    raw_details = getattr(exc, "details", None)
    if isinstance(raw_details, str) and raw_details.strip():
        details = raw_details.strip()
        match = re.search(r"Invalid API key", details, flags=re.IGNORECASE)
        if match:
            return "Invalid API key"
        return details

    text = str(exc).strip()
    if not text:
        return "Unknown Supabase API error"

    match = re.search(r"Invalid API key", text, flags=re.IGNORECASE)
    if match:
        return "Invalid API key"
    return text


def _is_unique_violation(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if str(code) == "23505":
        return True

    message = str(exc).lower()
    return "duplicate key value violates unique constraint" in message or "23505" in message


def _is_missing_table_error(exc: Exception, *, table_name: str) -> bool:
    code = str(getattr(exc, "code", "")).upper()
    if code == "PGRST205":
        return True

    message = str(exc).lower()
    return (
        f"could not find the table 'public.{table_name.lower()}'" in message
        or f"relation \"{table_name.lower()}\" does not exist" in message
    )
