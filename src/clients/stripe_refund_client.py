"""Stripe refund client — the GUEST incident-payout execution rail.

Refunds go back down the ORIGINAL trip payment (the payment intent stored by the marketplace on
the transaction's protectedData.stripePaymentIntents.default). No stripe SDK dependency — plain
REST (api.stripe.com/v1/refunds, form-encoded) with a caller-supplied Idempotency-Key so a retry
after a network blip can never double-refund.

Keys: STRIPE_API_KEY_AU / STRIPE_API_KEY_SG (market-scoped, from .env).
"""
import os
import logging

import requests

logger = logging.getLogger(__name__)

_KEY_ENV = {"au": "STRIPE_API_KEY_AU", "sg": "STRIPE_API_KEY_SG"}


class StripeRefundError(Exception):
    """Rail failure — the caller leaves the payout `approved` + retryable."""


def create_refund(*, payment_intent: str, amount_cents: int, market: str,
                  idempotency_key: str, metadata: dict | None = None,
                  timeout: int = 25) -> str:
    """Create a (full or partial) refund against the original payment intent; return the
    refund id (re_…). Raises StripeRefundError with Stripe's own error message on any failure
    (insufficient charge balance, already refunded, PI not found, …)."""
    key = os.getenv(_KEY_ENV.get((market or "").lower(), ""))
    if not key:
        raise StripeRefundError(f"no Stripe API key configured for market '{market}'")
    if not payment_intent or not str(payment_intent).startswith("pi_"):
        raise StripeRefundError(f"invalid payment intent '{payment_intent}'")
    if amount_cents <= 0:
        raise StripeRefundError("refund amount must be positive")
    data = {"payment_intent": payment_intent, "amount": int(amount_cents)}
    for k, v in (metadata or {}).items():
        data[f"metadata[{k}]"] = str(v)[:200]
    try:
        r = requests.post("https://api.stripe.com/v1/refunds", data=data,
                          headers={"Authorization": f"Bearer {key}",
                                   "Idempotency-Key": idempotency_key},
                          timeout=timeout)
    except requests.RequestException as e:
        raise StripeRefundError(f"Stripe unreachable: {e}") from e
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 200 and str(body.get("id", "")).startswith("re_"):
        return body["id"]
    err = body.get("error") or {}
    raise StripeRefundError(
        f"Stripe refund rejected ({r.status_code}): "
        f"{err.get('code') or err.get('type') or 'unknown'} — {err.get('message') or r.text[:200]}")
