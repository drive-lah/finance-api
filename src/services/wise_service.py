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


    # ── Outbound payouts (quote → recipient → transfer → fund) ─────────────────
    # All money-moving calls; guarded upstream by the payout service's dry-run flag.

    def _post(self, path: str, body: dict, extra_headers: Optional[dict] = None) -> dict:
        if not self.api_key:
            raise ValueError("WISE_API_KEY environment variable is not set")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        try:
            r = requests.post(f"{self.base_url}{path}", json=body, headers=headers, timeout=30)
            # SCA challenge: Wise replies 403 with x-2fa-approval; caller signs + retries.
            if r.status_code == 403 and r.headers.get("x-2fa-approval"):
                return {"__sca_required__": True, "token": r.headers["x-2fa-approval"]}
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            body_txt = e.response.text if e.response is not None else ""
            raise ValueError(f"Wise API {e.response.status_code} on {path}: {body_txt}") from e
        except requests.RequestException as e:
            raise ValueError(f"Wise API request failed: {e}") from e

    def create_quote(self, profile_id: int, source_ccy: str, target_ccy: str,
                     target_amount: float) -> dict:
        """Create a quote for paying `target_amount` in target_ccy. Same-ccy in v1."""
        return self._post(f"/v3/profiles/{profile_id}/quotes", {
            "sourceCurrency": source_ccy, "targetCurrency": target_ccy,
            "targetAmount": round(float(target_amount), 2), "payOut": "BANK_TRANSFER",
        })

    def list_recipients(self, profile_id: int, currency: Optional[str] = None) -> list[dict]:
        params = {"profileId": profile_id}
        if currency:
            params["currency"] = currency
        result = self._get("/v1/accounts", params)
        if isinstance(result, dict):
            result = result.get("content", result.get("accounts", []))
        return result if isinstance(result, list) else []

    def get_transfer(self, transfer_id) -> dict:
        """GET a transfer's current status (POL-130 poller). Returns the Wise transfer object incl
        `status` (incoming_payment_waiting / outgoing_payment_sent / funds_refunded / bounced_back / ...)."""
        r = self._get(f"/v1/transfers/{transfer_id}")
        return r if isinstance(r, dict) else {}

    def get_account_requirements(self, target_ccy: str, source_ccy: str = "AUD",
                                 amount: float = 1000) -> list:
        """PM-6: Wise's per-currency recipient shape. Returns the account TYPES valid for `target_ccy`
        (e.g. 'indian'/'philippines'/'aba'/'swift_code'), each with its required `fields`, so we can
        render the right form and validate BEFORE calling create_recipient (a 400 with the missing
        field, not a Wise 500). No quote needed — the temporary-quote form of the v1 endpoint."""
        r = self._get("/v1/account-requirements",
                      {"source": source_ccy, "target": target_ccy, "sourceAmount": amount})
        return r if isinstance(r, list) else (r.get("content", []) if isinstance(r, dict) else [])

    def create_recipient(self, profile_id: int, currency: str, account_holder_name: str,
                         account_type: str, details: dict) -> dict:
        """Register a recipient (bank account) on a Wise profile. Returns the created account incl `id`
        (the channel's recipient id). `account_type` + `details` are Wise's currency-specific shape
        (e.g. type='singapore' details={accountNumber}; type='australian' details={bsbCode,accountNumber};
        type='iban' details={IBAN}). No money moves. Recipients are IMMUTABLE — an edit is a new create."""
        return self._post("/v1/accounts", {
            "profile": int(profile_id), "currency": currency, "type": account_type,
            "accountHolderName": account_holder_name, "details": details,
        })

    def delete_recipient(self, account_id) -> bool:
        """Soft-delete (deactivate) a Wise recipient. Best-effort — Wise keeps it referenced by past
        transfers, so this only removes it from the active list."""
        if not self.api_key:
            raise ValueError("WISE_API_KEY environment variable is not set")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        r = requests.delete(f"{self.base_url}/v1/accounts/{account_id}", headers=headers, timeout=30)
        return r.status_code in (200, 204)

    def create_transfer(self, target_account_id: str, quote_id: str,
                        customer_txn_id: str, reference: str) -> dict:
        """Create a transfer (does NOT move money until funded).

        Wise requires customerTransactionId to be a UUID (it is Wise's idempotency key). Our
        idempotency_key is a human-readable string ("inv<id>-<ts>"), so derive a STABLE UUID from
        it (uuid5) — same key -> same UUID -> Wise treats a retry as the same transfer, not a new one.
        """
        import uuid
        try:
            ctid = str(uuid.UUID(str(customer_txn_id)))
        except (ValueError, AttributeError, TypeError):
            ctid = str(uuid.uuid5(uuid.NAMESPACE_OID, str(customer_txn_id)))
        return self._post("/v1/transfers", {
            "targetAccount": target_account_id, "quoteUuid": quote_id,
            "customerTransactionId": ctid,
            "details": {"reference": (reference or "")[:35]},
        })

    def _sign_2fa(self, token: str) -> str:
        """Sign the Wise x-2fa-approval token with our registered SCA private key (RSA-SHA256,
        PKCS1v15), base64-encoded — the second factor for API-initiated transfers (POL-87)."""
        import base64
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        key_path = os.environ.get("WISE_SCA_PRIVATE_KEY_PATH", "")
        if not key_path or not os.path.exists(key_path):
            raise ValueError("WISE_SCA_PRIVATE_KEY_PATH not set / key missing — register the SCA keypair first")
        with open(key_path, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        sig = private_key.sign(token.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode("ascii")

    def fund_transfer(self, profile_id: int, transfer_id: str) -> dict:
        """Fund a transfer from the Wise balance — the SCA-gated money-move step.

        Wise replies 403 + x-2fa-approval on the first call; we sign that token with the
        registered private key and retry with x-2fa-approval + X-Signature headers. One-time
        setup = upload the public key to Wise (PRD §5.5 / POL-87)."""
        path = f"/v3/profiles/{profile_id}/transfers/{transfer_id}/payments"
        first = self._post(path, {"type": "BALANCE"})
        if not first.get("__sca_required__"):
            return first
        signature = self._sign_2fa(first["token"])
        return self._post(path, {"type": "BALANCE"},
                          extra_headers={"x-2fa-approval": first["token"], "X-Signature": signature})


wise_service = WiseService()
