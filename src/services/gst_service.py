"""GST engine core (POL-119) — the ONE place that decides GST-applicability and computes the amount.

Both the live hooks (going-forward: bill → deferred, payment → claimable) and the batch re-derivation
(Apr–Jun 2026 + historical) call these functions, so the rule lives in exactly one place. Pure decision
+ arithmetic — NO ledger mutation here (callers post the JEs). Canonical spec: wip/GST_ENGINE.md.

GATES:
  input  (money out): invoiced -> the INVOICE's GST is the truth (tax>0 claim it; no GST on the
                      invoice -> claim only if the vendor is AU-registered, i.e. extraction miss;
                      unregistered vendor -> no claim, DQ-99); direct expense -> entity registered
                      AND account.gst_applicable_<mkt> AND vendor registered in mkt.
  output (money in)  = entity registered AND revenue account.gst_applicable_<mkt> (no vendor gate)
MARKET: entity 3 → AU, entities 1/2 → SG (SG posts zero today — not registered).
AMOUNT: 1/11 of the GST-inclusive cash (AU 10%). For invoiced purchases the invoice's own tax_amount wins.

ACCOUNTS: 1350 claimable input · 2500 payable output · 1355 deferred input · 2505 deferred output.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.services.counterparty_service import CounterpartyService

GST_INPUT = "1350"            # claimable input GST (cash paid)
GST_OUTPUT = "2500"           # payable output GST (cash collected)
GST_INPUT_DEFERRED = "1355"   # input GST on open (unpaid) purchase invoices
GST_OUTPUT_DEFERRED = "2505"  # output GST on open (uncollected) sales invoices


def market_for_entity(entity_id: Optional[int]) -> Optional[str]:
    return CounterpartyService.market_for_entity(entity_id)


def entity_is_gst_registered(db: Session, entity_id: Optional[int]) -> bool:
    r = db.execute(text("SELECT gst_rate FROM finance_entities WHERE id=:id"), {"id": entity_id}).scalar()
    return bool(r and float(r) > 0)


def account_gst_applicable(db: Session, account_code: Optional[str], market: Optional[str]) -> bool:
    if not account_code or not market:
        return False
    # PR-10: `col` is one of exactly two literals (allowlisted by the ternary), never caller input —
    # safe to interpolate into the column position, which cannot be parameterised.
    col = "gst_applicable_au" if market.upper() == "AU" else "gst_applicable_sg"
    r = db.execute(
        text(f"SELECT {col} FROM finance_accounts WHERE code=:c ORDER BY (entity_id IS NULL) DESC LIMIT 1"),
        {"c": account_code},
    ).scalar()
    return bool(r)


# POL-123 FINAL (Gaurav, 2026-08-15): NO refund special-casing anywhere — no COA flag, no event set,
# no hardcoding. A refund out is treated by the pure machine as cash-out = input GST (Dr 1350).
# Box 7 net GST is IDENTICAL to the reversal treatment (the 1A and 1B effects cancel exactly);
# the accepted trade-off is grossed-up 1A/1B and a derived G1 that includes refunds. The bank lane's
# input decision is the vendor gate over a correct vendor list (DQ-101); nothing else.


def vendor_registered(db: Session, counterparty_id: Optional[int], market: Optional[str]) -> bool:
    if not counterparty_id or not market:
        return False
    from src.models.counterparty import FinanceCounterparty
    cp = db.get(FinanceCounterparty, counterparty_id)
    return CounterpartyService.registered_in(cp, market)


def gst_from_gross(gross: float) -> float:
    """AU GST = 1/11 of a GST-inclusive amount, rounded to cents."""
    g = Decimal(str(gross or 0)) / Decimal("11")
    return float(g.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ── the two gate decisions ────────────────────────────────────────────────────
def input_gst_applies(db: Session, entity_id: Optional[int], account_code: Optional[str],
                      counterparty_id: Optional[int]) -> bool:
    """Direct-expense / purchase gate — all three conditions (POL-119)."""
    mkt = market_for_entity(entity_id)
    return (
        entity_is_gst_registered(db, entity_id)
        and account_gst_applicable(db, account_code, mkt)
        and vendor_registered(db, counterparty_id, mkt)
    )


def output_gst_applies(db: Session, entity_id: Optional[int], account_code: Optional[str]) -> bool:
    """Sales gate — two conditions (no vendor; it's our own supply)."""
    mkt = market_for_entity(entity_id)
    return entity_is_gst_registered(db, entity_id) and account_gst_applicable(db, account_code, mkt)


def describe(db: Session, entity_id, account_code, counterparty_id, gross, direction: str) -> dict:
    """One-shot: does GST apply on this cash line, and how much? direction = 'input' | 'output'."""
    if direction == "input":
        applies = input_gst_applies(db, entity_id, account_code, counterparty_id)
    else:
        applies = output_gst_applies(db, entity_id, account_code)
    return {
        "applies": applies,
        "market": market_for_entity(entity_id),
        "gst": gst_from_gross(gross) if applies else 0.0,
        "net": round(float(gross or 0) - (gst_from_gross(gross) if applies else 0.0), 2),
    }


# ── the ONE locked decision function (GST_ENGINE.md, four accounts) ────────────
def _hit(account: str, side: str, amount: Decimal, verdict: str, reason: str) -> dict:
    return {"account": account, "side": side, "amount": float(amount.quantize(Decimal("0.01"))),
            "verdict": verdict, "reason": reason}


def _none(reason: str) -> dict:
    return {"account": None, "side": None, "amount": 0.0, "verdict": "EXCLUDED", "reason": reason}


def _review(reason: str) -> dict:
    return {"account": None, "side": None, "amount": 0.0, "verdict": "REVIEW", "reason": reason}


def classify(*, entity_registered: bool, account_applicable: bool, direction: str,
             leg_touches_bank: bool, gross, invoice_tax=None, has_invoice: bool = False,
             vendor_registered_flag: Optional[bool] = None, is_refund: bool = False,
             is_deposit: bool = False, is_host_payout: bool = False,
             claim_host_by_default: bool = True) -> dict:
    """The single going-forward GST decision (locked model). Pure — no DB, no posting.

    direction : 'output' (revenue / money-in) | 'input' (expense / money-out)
    Routing   : realized (bank leg) -> 1350/2500 ; accrual leg -> 1355/2505.
    Returns {account, side, amount, verdict, reason}. account=None means no GST posted.
    """
    if is_deposit:
        return _none("deposit — not a supply; no GST until forfeited")
    if not entity_registered:
        return _none("entity not GST-registered (SG)")
    if not account_applicable:
        return _none("account not gst-applicable")

    amount = (Decimal(str(invoice_tax)) if (has_invoice and invoice_tax is not None)
              else Decimal(str(gst_from_gross(gross))))
    if amount <= 0:
        return _none("zero GST amount")

    realized = leg_touches_bank

    # DORMANT BY DESIGN (POL-123 final, Gaurav 2026-08-15): the live engine NEVER passes
    # is_refund — refunds land as input on cash-out (box 7 net identical). This branch is kept
    # ONLY for the historical H1 reconciliation scripts (gst_h1_pertxn), which posted refunds as
    # output reversals. Do NOT wire it into live posting.
    if is_refund:
        acct = GST_OUTPUT if realized else GST_OUTPUT_DEFERRED
        return _hit(acct, "debit", amount, "output_reversal", "refund/chargeback reduces output GST")

    if direction == "output":
        acct = GST_OUTPUT if realized else GST_OUTPUT_DEFERRED
        return _hit(acct, "credit", amount, "output", "output GST on revenue")

    # input — the INVOICE is the source of truth; the vendor gate is supreme (DQ-99).
    #   invoice_tax > 0           -> claim exactly that (amount above already used it).
    #   invoice_tax None/0        -> the invoice showed no GST. Fall back to 1/11×gross ONLY when
    #                                the vendor IS AU-registered (a genuine extraction miss). An
    #                                unregistered/foreign vendor genuinely charged no GST -> no claim.
    #   direct expense (no invoice) -> claim only if the vendor is AU-registered.
    if not is_host_payout:
        invoice_had_gst = has_invoice and invoice_tax is not None and float(invoice_tax) > 0
        if not invoice_had_gst:
            if vendor_registered_flag is None:
                return _review("expense, no counterparty — attach then decide")
            if not vendor_registered_flag:
                if has_invoice:
                    return _none("invoiced purchase shows no GST + vendor not AU-registered — no input GST (DQ-99)")
                return _review("direct expense, vendor not AU-registered — reverse-charge/review")
    if is_host_payout and not claim_host_by_default and not vendor_registered_flag:
        return _review("host payout, host not registered — RCTI required")
    acct = GST_INPUT if realized else GST_INPUT_DEFERRED
    return _hit(acct, "debit", amount, "input", "claimable input GST")
