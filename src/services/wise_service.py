"""
Wise (TransferWise) API integration service.

Handles:
  - Profile discovery (GET /v1/profiles)
  - Balance listing per profile (GET /v4/profiles/{id}/balances)
  - Statement fetching per balance (balance-statements endpoint)
  - Normalizing API responses to NormalizedRow for import

API key is read from WISE_API_KEY environment variable.
Per-account credentials (profile_id, balance_id) are stored in
FinanceBankAccount.api_credentials JSON column.

Wise statement transaction → NormalizedRow mapping:
  transaction_date  ← date (ISO 8601, truncated to date)
  description       ← details.description
  amount            ← amount.value (already signed: negative=out, positive=in)
  reference_number  ← details.paymentReference (user-set ref, e.g. "DL HBD RT27435")
  currency          ← amount.currency
  counterparty_name ← details.recipient.name (DEBIT) or details.senderName (CREDIT)
  transaction_type  ← details.type (TRANSFER, DEPOSIT, CONVERSION, MONEY_ADDED)
  running_balance   ← runningBalance.value
  source_id         ← referenceNumber (TransferWise ID, e.g. "TRANSFER-1941949771")
                      Used as the sole fingerprint — globally unique per transaction.
"""
import logging
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import requests

from src.services.csv_adapters.base import NormalizedRow

logger = logging.getLogger(__name__)

WISE_BASE_URL = "https://api.transferwise.com"


class WiseService:
    """Wise API client and transaction normalizer."""

    def __init__(self) -> None:
        self.api_key = os.environ.get("WISE_API_KEY", "")
        self.base_url = WISE_BASE_URL

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: Optional[dict] = None) -> dict | list:
        """Make an authenticated GET request. Raises ValueError on API errors."""
        if not self.api_key:
            raise ValueError("WISE_API_KEY environment variable is not set")

        url = f"{self.base_url}{path}"
        try:
            response = requests.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as e:
            body = e.response.text if e.response is not None else ""
            raise ValueError(f"Wise API {e.response.status_code} on {path}: {body}") from e
        except requests.RequestException as e:
            raise ValueError(f"Wise API request failed: {e}") from e

    # ── Profile ───────────────────────────────────────────────────────────────

    def get_profiles(self) -> list[dict]:
        """
        Return all profiles associated with this API key.

        Uses /v2/profiles which returns all profiles including multiple
        business profiles. /v1/profiles only returns a subset.
        Normalises type to lowercase for consistent comparison.
        """
        result = self._get("/v2/profiles")
        profiles = result if isinstance(result, list) else []
        # v2 returns type as uppercase ("BUSINESS", "PERSONAL") — normalise
        for p in profiles:
            if isinstance(p.get("type"), str):
                p["type"] = p["type"].lower()
        return profiles

    def get_business_profiles(self) -> list[dict]:
        """Return all business profiles (excludes personal)."""
        return [p for p in self.get_profiles() if p.get("type") == "business"]

    def get_business_profile(self) -> dict:
        """Return the first business profile (kept for backwards compat)."""
        profiles = self.get_business_profiles()
        if profiles:
            return profiles[0]
        raise ValueError("No business profiles found for this API key")

    # ── Balances ──────────────────────────────────────────────────────────────

    def get_balances(self, profile_id: int) -> list[dict]:
        """Return all STANDARD balances for a profile."""
        result = self._get(f"/v4/profiles/{profile_id}/balances", {"types": "STANDARD"})
        return result if isinstance(result, list) else []

    # ── Statement ─────────────────────────────────────────────────────────────

    def get_statement(
        self,
        profile_id: int,
        balance_id: int,
        date_from: date,
        date_to: date,
    ) -> dict:
        """
        Fetch balance statement for a date range.

        intervalStart/End must be ISO 8601 with timezone. We use UTC midnight.
        """
        params = {
            "intervalStart": f"{date_from.isoformat()}T00:00:00.000Z",
            "intervalEnd": f"{date_to.isoformat()}T23:59:59.999Z",
        }
        result = self._get(
            f"/v1/profiles/{profile_id}/balance-statements/{balance_id}/statement.json",
            params,
        )
        return result if isinstance(result, dict) else {}

    # ── Normalization ─────────────────────────────────────────────────────────

    def normalize_statement(
        self,
        statement: dict,
        expected_currency: str,
    ) -> tuple[list[NormalizedRow], list[dict]]:
        """
        Convert a Wise statement dict to NormalizedRow instances.

        Skips rows where amount.currency != expected_currency (shouldn't happen
        since the statement is per-balance, but defensive).

        Returns (rows, errors).
        """
        rows: list[NormalizedRow] = []
        errors: list[dict] = []

        # Wise returns transactions NEWEST-FIRST. Reverse to chronological so
        # insertion order (and thus id order) matches real sequence — the
        # 2026-07-26 recon checkpoint caught intraday id-order running backwards,
        # which broke balance-chain checks and first/last-row-of-day selection.
        for i, txn in enumerate(reversed(statement.get("transactions", [])), start=1):
            try:
                row = self._normalize_txn(txn, expected_currency)
                if row is not None:
                    rows.append(row)
            except Exception as exc:
                errors.append({"index": i, "error": str(exc), "reference": txn.get("referenceNumber")})

        return rows, errors

    def _normalize_txn(self, txn: dict, expected_currency: str) -> Optional[NormalizedRow]:
        """Normalize one Wise API transaction to NormalizedRow. Returns None to skip."""
        # --- currency ---
        amount_obj = txn.get("amount", {})
        currency = amount_obj.get("currency", "")
        if currency != expected_currency:
            return None

        # --- amount (already signed: negative=out, positive=in) ---
        amount = Decimal(str(amount_obj.get("value", 0)))
        if amount == Decimal("0"):
            return None  # zero-amount rows have no ledger impact

        # --- date (ISO 8601 e.g. "2026-01-28T17:20:13.338Z") ---
        raw_date = txn.get("date", "")
        transaction_date = datetime.fromisoformat(
            raw_date.replace("Z", "+00:00")
        ).date()

        # --- details ---
        details = txn.get("details", {})
        description = details.get("description", "").strip() or ""
        payment_reference = details.get("paymentReference", "") or ""
        payment_reference = payment_reference.strip() or None
        txn_type = (details.get("type") or "").strip() or None

        # --- counterparty: Payee for debits, Payer for credits ---
        counterparty_name: Optional[str] = None
        if txn.get("type") == "DEBIT":
            recipient = details.get("recipient") or {}
            counterparty_name = (recipient.get("name") or "").strip() or None
        else:
            counterparty_name = (details.get("senderName") or "").strip() or None

        # --- running balance ---
        rb_obj = txn.get("runningBalance") or {}
        rb_raw = rb_obj.get("value")
        running_balance = Decimal(str(rb_raw)) if rb_raw is not None else None

        # --- source_id = TransferWise ID (globally unique, used as fingerprint) ---
        source_id = (txn.get("referenceNumber") or "").strip() or None

        return NormalizedRow(
            transaction_date=transaction_date,
            description=description,
            amount=amount,
            reference_number=payment_reference,
            currency=currency,
            counterparty_name=counterparty_name,
            transaction_type=txn_type,
            running_balance=running_balance,
            source_id=source_id,
        )


wise_service = WiseService()
