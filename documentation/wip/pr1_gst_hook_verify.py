"""PR-1 verification — the payment-time GST hook decision logic across all three draft-JE sites.

DB gates in gst_service are stubbed (no database needed — the SQLite harness can't build the schema).
classify() itself runs for real, so this proves the wiring: direction, vendor gate, override, scaling.
Run: ./venv/bin/python documentation/wip/pr1_gst_hook_verify.py
"""
from decimal import Decimal
from types import SimpleNamespace

from src.services import gst_service

# ── stub the DB-reading gates; classify() stays real ──────────────────────────
_STATE = {"entity_registered": True, "account_applicable": True, "vendor_registered": True,
          "market": "au", "applicable_codes": None, "reverses": None, "claim_default": None}
gst_service.entity_is_gst_registered = lambda db, e: _STATE["entity_registered"]
gst_service.account_gst_applicable = (
    lambda db, c, m: (c in _STATE["applicable_codes"]) if _STATE["applicable_codes"] is not None
    else _STATE["account_applicable"])
gst_service.vendor_registered = lambda db, cp, m: _STATE["vendor_registered"]
gst_service.market_for_entity = lambda e: _STATE["market"]

from src.services.categorization_service import CategorizationService
from src.services.invoice_service import InvoiceService
from src.services.economic_events.service import EconomicEventService

cat = CategorizationService()
inv = InvoiceService()
eco = EconomicEventService()

fails = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}: got {got}  want {want}")
    if not ok:
        fails.append(name)


def setstate(**kw):
    _STATE.update({"entity_registered": True, "account_applicable": True, "vendor_registered": True,
                   "market": "au", "applicable_codes": None, "reverses": None, "claim_default": None})
    _STATE.update(kw)


print("CATEGORIZATION _resolve_gst (direct bank txn):")
setstate()
check("input, registered vendor → 1350/10", cat._resolve_gst(None, entity_id=3, contra_code="5000",
      counterparty_id=10, abs_amount=110.0, direction="input"), ("1350", 10.0))
setstate(vendor_registered=False)
check("input, UNregistered vendor → no claim (DQ-99)", cat._resolve_gst(None, entity_id=3, contra_code="5000",
      counterparty_id=10, abs_amount=110.0, direction="input"), (None, 0.0))
setstate()
check("input, NO counterparty → REVIEW → no claim", cat._resolve_gst(None, entity_id=3, contra_code="5000",
      counterparty_id=None, abs_amount=110.0, direction="input"), (None, 0.0))
check("output, applicable revenue → 2500/10 (no vendor gate)", cat._resolve_gst(None, entity_id=3,
      contra_code="4000", counterparty_id=None, abs_amount=110.0, direction="output"), ("2500", 10.0))
setstate(account_applicable=False)
check("account not applicable → no GST", cat._resolve_gst(None, entity_id=3, contra_code="6600",
      counterparty_id=10, abs_amount=110.0, direction="input"), (None, 0.0))
setstate()
check("override=False → suppress", cat._resolve_gst(None, entity_id=3, contra_code="5000",
      counterparty_id=10, abs_amount=110.0, direction="input", gst_override=False), (None, 0.0))
check("override=True → force 1350/10", cat._resolve_gst(None, entity_id=3, contra_code="6000",
      counterparty_id=None, abs_amount=110.0, direction="input", gst_override=True), ("1350", 10.0))
setstate(entity_registered=False)
check("override=True but entity NOT registered → no GST", cat._resolve_gst(None, entity_id=1,
      contra_code="6000", counterparty_id=None, abs_amount=110.0, direction="input", gst_override=True), (None, 0.0))

print("\nINVOICE _input_gst_reclass (gross-booked bill paid):")
setstate()
bill = SimpleNamespace(contra_account_code="5000", total_amount=110.0, tax_amount=10.0, entity_id=3, counterparty_id=10)
check("full payment → (5000, 10.0)", inv._input_gst_reclass(None, bill, 110.0), ("5000", 10.0))
check("half payment → GST scales to 5.0", inv._input_gst_reclass(None, bill, 55.0), ("5000", 5.0))
no_tax = SimpleNamespace(contra_account_code="5000", total_amount=110.0, tax_amount=None, entity_id=3, counterparty_id=10)
check("no invoice tax + registered vendor → fall back to 1/11 (5000, 10.0)", inv._input_gst_reclass(None, no_tax, 110.0), ("5000", 10.0))
setstate(vendor_registered=False)
check("no invoice tax + UNregistered vendor → no claim (DQ-99)", inv._input_gst_reclass(None, no_tax, 110.0), (None, 0.0))

print("\nECONOMIC-EVENTS _lane_a_gst (POL-123 Lane A: bank-leg gate + contra flag + 1/11):")
BANK = {"1019", "1020"}
setstate(applicable_codes={"4000", "5000"})
check("cash IN (Dr 1019 / Cr 4000) → output (2500, 10, credit)",
      eco._lane_a_gst(None, 3, "1019", "4000", Decimal("110"), BANK), ("2500", Decimal("10"), "credit"))
check("cash OUT (Dr 5000 / Cr 1019) → input (1350, 10, debit)",
      eco._lane_a_gst(None, 3, "5000", "1019", Decimal("110"), BANK), ("1350", Decimal("10"), "debit"))
check("accrual (Dr 1200 / Cr 4022) no bank leg → none",
      eco._lane_a_gst(None, 3, "1200", "4022", Decimal("110"), BANK), (None, Decimal("0"), None))
check("clearing settle (Dr 1019 / Cr 1200) contra not applicable → none",
      eco._lane_a_gst(None, 3, "1019", "1200", Decimal("110"), BANK), (None, Decimal("0"), None))
check("transfer (Dr 1019 / Cr 1020) both bank → none",
      eco._lane_a_gst(None, 3, "1019", "1020", Decimal("110"), BANK), (None, Decimal("0"), None))
setstate(entity_registered=False, applicable_codes={"4000"})
check("entity not registered (SG) → none",
      eco._lane_a_gst(None, 1, "1019", "4000", Decimal("110"), BANK), (None, Decimal("0"), None))

print("\nPOL-123 FINAL — no flags, no refund special-case, vendor gate is the whole bank-lane input decision:")
# Refund JE shape (Dr Refunds-Trip / Cr Bank) lands as INPUT by design — box 7 net identical
setstate(applicable_codes={"5052"})
check("Lane A refund-shaped event → input (1350, 10, debit) BY DESIGN",
      eco._lane_a_gst(None, 3, "5052", "1019", Decimal("110"), BANK),
      ("1350", Decimal("10"), "debit"))
# Lane B: NO claim-by-default — an unregistered vendor on a payout account is refused (vendor gate is all)
setstate(vendor_registered=False)
check("Lane B payout acct, UNregistered vendor → NO claim (vendor gate supreme)",
      cat._resolve_gst(None, entity_id=3, contra_code="5001", counterparty_id=10,
                       abs_amount=110.0, direction="input"), (None, 0.0))
setstate()
check("Lane B payout acct, REGISTERED vendor → claims (1350, 10)",
      cat._resolve_gst(None, entity_id=3, contra_code="5001", counterparty_id=10,
                       abs_amount=110.0, direction="input"), ("1350", 10.0))

print("\n" + ("ALL PASS ✓" if not fails else f"FAILURES: {fails}"))
raise SystemExit(1 if fails else 0)
