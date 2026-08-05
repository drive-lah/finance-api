#!/usr/bin/env python
"""
VR-2 READ-ONLY probe: does the payment-reference signal actually carry invoice numbers?

Two questions, answered from data (no writes, no JEs):
  1. What do invoice_numbers look like on the 1,112 ready invoices? (format, length, distinctness)
  2. On counterparty-stamped OUTFLOW txns, is the invoice number present in
     reference_number / description / original_csv_row?

Then a dry-run reference match: for each ready invoice, find outflow txns of the
SAME counterparty whose reference/description contains the invoice_number token.
Reports lift vs the exact-amount pass. Nothing is persisted.
"""
import os
import re
import csv
import json
from collections import Counter
from datetime import date
from decimal import Decimal

STUB = date(1901, 1, 1)  # invoice_date <= this = undated stub (POL-74)

import psycopg2
from psycopg2.extras import RealDictCursor

DB = os.environ["DATABASE_URL"] if "DATABASE_URL" in os.environ else None
if not DB:
    # load from .env
    for line in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
        if line.startswith("DATABASE_URL="):
            DB = line.strip().split("=", 1)[1].strip().strip('"')
            break

conn = psycopg2.connect(DB)
cur = conn.cursor(cursor_factory=RealDictCursor)

# Approximate period-blended mid rates -> SGD (2019-2026). For PLAUSIBILITY only:
# a genuine FX match lands within a few %, a wrong reference match is off by tens of %.
FX_TO_SGD = {"SGD":1.0, "USD":1.34, "AUD":0.90, "NZD":0.83,
             "INR":0.0161, "MYR":0.30, "EUR":1.45, "GBP":1.68}
def to_sgd(amount, ccy):
    r = FX_TO_SGD.get((ccy or "SGD").upper())
    return round(abs(float(amount)) * r, 2) if r else None
def neutralize(inv_amt, inv_ccy, txn_amt, txn_ccy):
    """returns (inv_sgd, txn_sgd, diff_sgd, diff_pct, flag)"""
    i = to_sgd(inv_amt, inv_ccy); t = to_sgd(txn_amt, txn_ccy)
    if i is None or t is None or i == 0:
        return (i, t, "", "", "NO_RATE")
    diff = round(t - i, 2); pct = round(diff / i * 100, 1)
    same = (inv_ccy or "").upper() == (txn_ccy or "").upper()
    if abs(pct) <= 1.0:      flag = "TIE"                      # effectively equal
    elif abs(pct) <= 6.0:    flag = "TIE_FX" if not same else "NEAR"   # within FX/rounding band
    elif pct > 6.0:          flag = "TXN_GT"                   # payment bigger (bundled?) or wrong match
    else:                    flag = "TXN_LT"                   # payment smaller (partial?) or wrong match
    return (i, t, diff, pct, flag)

READY = """
  (ai_extraction_raw->'provisional_paid'->>'is_provisional_paid'='true')
  AND counterparty_id IS NOT NULL
  AND total_amount IS NOT NULL AND total_amount>0
  AND pdf_s3_key IS NOT NULL
"""

# ---- Q1: invoice_number shape on ready invoices ----
cur.execute(f"""
  SELECT id, counterparty_id, invoice_number, total_amount, currency, invoice_date
  FROM finance_invoices
  WHERE {READY}
""")
invoices = cur.fetchall()
print(f"ready invoices: {len(invoices)}")

have_num = [i for i in invoices if (i["invoice_number"] or "").strip()]
print(f"  with non-empty invoice_number: {len(have_num)}")
lens = Counter(len((i['invoice_number'] or '').strip()) for i in have_num)
print(f"  invoice_number length distribution (top): {lens.most_common(10)}")
print("  sample invoice_numbers:")
for i in have_num[:25]:
    print(f"    [{i['id']}] cp={i['counterparty_id']} num={i['invoice_number']!r} amt={i['total_amount']} {i['currency']}")

def norm(s):
    """uppercase, strip everything non-alnum — 'INV 00561671' -> 'INV00561671'."""
    return re.sub(r'[^A-Za-z0-9]', '', (s or '')).upper()

# Specific tokens that identify THIS invoice, not a shared vendor prefix:
#  - the full normalized invoice number (best signal)
#  - any numeric run of >=5 digits (distinctive; kills 4-digit date/short collisions)
def tokens(s):
    if not s:
        return []
    full = norm(s)
    out = []
    if len(full) >= 5:
        out.append(full)
    for run in re.findall(r'\d{5,}', s):
        out.append(run)
    return list(dict.fromkeys(out))  # dedupe, keep order

# ---- Q2: what's in txn reference/description on counterparty outflows ----
cur.execute("""
  SELECT reference_number, description, original_csv_row
  FROM finance_transactions
  WHERE counterparty_id IS NOT NULL AND amount < 0
  LIMIT 4000
""")
sample = cur.fetchall()
ref_pop = sum(1 for r in sample if (r["reference_number"] or "").strip())
print(f"\noutflow-with-counterparty sample: {len(sample)}")
print(f"  reference_number populated: {ref_pop} ({100*ref_pop/max(len(sample),1):.0f}%)")
print("  sample reference / description:")
for r in sample[:20]:
    print(f"    ref={ (r['reference_number'] or '')[:40]!r}  desc={(r['description'] or '')[:70]!r}")

# ---- Q3: DRY-RUN reference match ----
# For each ready invoice with a real invoice_number, search its OWN counterparty's
# outflow txns for the invoice-number token in ref/desc/raw. Amount-agnostic first
# (pure reference signal), then note whether amount also fits.
print("\n=== DRY-RUN REFERENCE MATCH (per-counterparty, token in ref/desc/raw) ===")
hits = 0
hit_rows = []
invoices_with_token = 0
for inv in invoices:
    toks = tokens(inv["invoice_number"])
    if not toks:
        continue
    invoices_with_token += 1
    cur.execute("""
      SELECT id, transaction_date, amount, currency, reference_number, description, original_csv_row
      FROM finance_transactions
      WHERE counterparty_id = %s AND amount < 0
    """, (inv["counterparty_id"],))
    cptxns = cur.fetchall()
    inv_date = inv["invoice_date"]
    inv_dated = inv_date is not None and inv_date > STUB  # real, checkable date
    matched = []
    for t in cptxns:
        hay = norm(" ".join([
            (t["reference_number"] or ""),
            (t["description"] or ""),
            (t["original_csv_row"] or ""),
        ]))
        if any(tok in hay for tok in toks):
            amt_fit = abs(abs(float(t["amount"])) - float(inv["total_amount"])) < 0.01
            # temporal: payment must be ON/AFTER invoice date (can't pay a not-yet-issued invoice)
            if inv_dated:
                temporal_ok = t["transaction_date"] >= inv_date  # True/False
            else:
                temporal_ok = None  # stub-dated invoice → cannot validate
            matched.append((t, amt_fit, temporal_ok))
    if matched:
        hits += 1
        def _absdiffpct(m):
            r = neutralize(inv["total_amount"], inv["currency"], m[0]["amount"], m[0]["currency"])
            return abs(r[3]) if isinstance(r[3], (int, float)) else 9e9
        # HARD GATE: prefer temporally-valid candidates (payment on/after invoice).
        # None (stub) is allowed into the pool; only explicit False is excluded.
        valid = [m for m in matched if m[2] is not False]
        pool = valid if valid else matched          # all-invalid → keep to surface the BLOCK
        best_m = min(pool, key=_absdiffpct)
        best = best_m[0]
        if best_m[2] is False:      temporal = "BLOCK"     # every candidate predates the invoice
        elif best_m[2] is None:     temporal = "UNKNOWN"   # stub-dated invoice
        else:                       temporal = "OK"
        i_sgd, t_sgd, diff_sgd, diff_pct, flag = neutralize(
            inv["total_amount"], inv["currency"], best["amount"], best["currency"])
        gap_days = (best["transaction_date"] - inv_date).days if inv_dated else ""
        hit_rows.append({
            "invoice_id": inv["id"], "cp": inv["counterparty_id"],
            "invoice_number": inv["invoice_number"],
            "invoice_date": inv_date, "pay_date": best["transaction_date"],
            "pay_minus_inv_days": gap_days, "temporal": temporal,
            "inv_ccy": inv["currency"], "inv_amt": str(inv["total_amount"]),
            "txn_id": best["id"],
            "txn_ccy": best["currency"], "txn_amt": str(abs(float(best["amount"]))),
            "inv_amt_sgd": i_sgd, "txn_amt_sgd": t_sgd,
            "diff_sgd": diff_sgd, "diff_pct": diff_pct, "fx_flag": flag,
            "same_ccy": inv["currency"] == best["currency"],
            "n_txn_matches": len(matched),
            "any_amount_fits": any(m[1] for m in matched),
            "txn_ref": (best["reference_number"] or "")[:50],
        })

print(f"ready invoices with a usable token: {invoices_with_token}")
print(f"invoices with >=1 reference match to their counterparty: {hits} "
      f"({100*hits/max(invoices_with_token,1):.1f}%)")
amt_and_ref = sum(1 for h in hit_rows if h["any_amount_fits"])
print(f"  of those, reference AND amount both fit: {amt_and_ref}")

# ---- amount-off bucket: how much is just CURRENCY MISMATCH (FX)? ----
off = [h for h in hit_rows if not h["any_amount_fits"]]
off_diff = [h for h in off if not h["same_ccy"]]
off_same = [h for h in off if h["same_ccy"]]
print(f"\n=== amount-off reference hits: {len(off)} ===")
print(f"  DIFFERENT currency (inv vs best txn) = FX conversion: {len(off_diff)}")
print(f"  SAME currency, still off (real partial/bundled/GST): {len(off_same)}")
# FX plausibility on diff-ccy: is txn/inv within a sane FX band?
import collections
pair = collections.Counter((h["inv_ccy"], h["txn_ccy"]) for h in off_diff)
print("  top currency pairs (inv->txn):")
for (a, b), n in pair.most_common(8):
    print(f"    {a}->{b}: {n}")
# same-ccy reason split
sb = collections.Counter()
for h in off_same:
    inv = abs(float(h["inv_amt"])); txn = abs(float(h["txn_amt"]))
    if inv == 0: sb["?"] += 1; continue
    r = txn/inv
    if 0.99 <= r <= 1.01: sb["~equal (rounding/cents)"] += 1
    elif 1.08 <= r <= 1.12: sb["txn +~10% (GST added)"] += 1
    elif 0.88 <= r <= 0.92: sb["txn -~10% (WHT/GST-in)"] += 1
    elif r > 1.12: sb["txn > inv (bundled/multi)"] += 1
    else: sb["txn < inv (partial)"] += 1
print("  same-currency amount-off reasons:")
for k, v in sb.most_common():
    print(f"    {k}: {v}")

# ---- FULL RECONCILIATION over ALL 1,112 ready invoices ----
# Re-walk every ready invoice, assign a mutually-exclusive bucket so the balance ties.
hit_by_inv = {h["invoice_id"]: h for h in hit_rows}
buckets = {"REF+EXACT_AMT": [], "REF+AMT_OFF": [], "NO_REF_MATCH": [],
           "TOKEN_TOO_SHORT": [], "NO_INVOICE_NUMBER": []}
recon_rows = []
for inv in invoices:
    num = (inv["invoice_number"] or "").strip()
    toks = tokens(inv["invoice_number"])
    h = hit_by_inv.get(inv["id"])
    if not num:
        b = "NO_INVOICE_NUMBER"
    elif not toks:
        b = "TOKEN_TOO_SHORT"
    elif h and h["any_amount_fits"]:
        b = "REF+EXACT_AMT"
    elif h:
        b = "REF+AMT_OFF"
    else:
        b = "NO_REF_MATCH"
    buckets[b].append(inv["id"])
    recon_rows.append({
        "invoice_id": inv["id"], "counterparty_id": inv["counterparty_id"],
        "invoice_number": num, "invoice_date": inv["invoice_date"],
        "inv_ccy": inv["currency"], "inv_amount": str(inv["total_amount"]),
        "pay_ccy": h["txn_ccy"] if h else "",
        "pay_amount": h["txn_amt"] if h else "",
        "inv_amt_sgd": h["inv_amt_sgd"] if h else "",
        "pay_amt_sgd": h["txn_amt_sgd"] if h else "",
        "diff_sgd": h["diff_sgd"] if h else "",
        "diff_pct": h["diff_pct"] if h else "",
        "fx_flag": h["fx_flag"] if h else "",
        "temporal": h["temporal"] if h else "",
        "pay_date": h["pay_date"] if h else "",
        "pay_minus_inv_days": h["pay_minus_inv_days"] if h else "",
        "bucket": b,
        "matched_txn_id": h["txn_id"] if h else "",
        "matched_txn_ref": h["txn_ref"] if h else "",
        "n_txn_ref_matches": h["n_txn_matches"] if h else 0,
    })

print("\n=== TEMPORAL GATE (payment must be ON/AFTER invoice date) ===")
tc = collections.Counter(h["temporal"] for h in hit_rows)
for k in ("OK", "UNKNOWN", "BLOCK"):
    print(f"  {k:8s}: {tc.get(k,0)}")
# borderline: BLOCKs where invoice is only slightly after payment (issue-lag grace zone)
blk = [h for h in hit_rows if h["temporal"] == "BLOCK"]
near = [h for h in blk if isinstance(h["pay_minus_inv_days"], int) and h["pay_minus_inv_days"] >= -7]
print(f"  of {len(blk)} BLOCK: {len(near)} are within 7 days (issue-lag grey zone), "
      f"{len(blk)-len(near)} are clearly before")
# clean = reference hit + amount ties + temporally OK
clean = [h for h in hit_rows if h["temporal"] == "OK" and h["fx_flag"] in ("TIE","TIE_FX","NEAR")]
print(f"  CLEAN (ref + amount-tie + temporal OK): {len(clean)}")

print("\n=== NEUTRALIZED (SGD) fx_flag distribution over 662 ref hits ===")
fc = collections.Counter(h["fx_flag"] for h in hit_rows)
for k, v in fc.most_common():
    print(f"  {k:8s}: {v}")
print("  TIE/TIE_FX/NEAR = tie within FX/rounding band; TXN_GT/TXN_LT = real gap → review")

print("\n=== BALANCE: all 1,112 ready invoices accounted for ===")
tot = 0
for b, ids in buckets.items():
    print(f"  {b:18s}: {len(ids)}")
    tot += len(ids)
print(f"  {'TOTAL':18s}: {tot}")

# write full reconciliation (all 1,112)
recon_out = os.path.join(os.path.dirname(__file__), "..",
    "documentation/wip/wave2_ready_invoice_reconciliation_2026-08-02.csv")
with open(recon_out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(recon_rows[0].keys()))
    w.writeheader()
    for r in recon_rows:
        w.writerow(r)
print(f"\nwrote ALL {len(recon_rows)} ready invoices -> {recon_out}")

# also the matched-only file (662)
out = os.path.join(os.path.dirname(__file__), "..",
                   "documentation/wip/wave2_dryrun_reference_match_2026-08-02.csv")
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(hit_rows[0].keys()) if hit_rows else
                       ["invoice_id"])
    w.writeheader()
    for h in hit_rows:
        w.writerow(h)
print(f"wrote {len(hit_rows)} matched -> {out}")

conn.close()
