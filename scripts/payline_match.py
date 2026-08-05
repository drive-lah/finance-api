#!/usr/bin/env python3
"""
PAYLINE — READ-ONLY reconciliation proposal generator.

For every provisionally-paid, UNMATCHED, counterparty-identified invoice that is
NOT a flagged duplicate or already claimed, produce a payment proposal with basis,
amount delta, and confidence.  No writes, no JEs, no Retool API calls.

Output: documentation/wip/PAYLINE_PROPOSALS.csv
"""

import os
import re
import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from itertools import combinations

import psycopg2
from psycopg2.extras import RealDictCursor

# ──────────────────────────────────────────────────────────────────────────────
# DB connection
# ──────────────────────────────────────────────────────────────────────────────
DB_URL = None
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
for line in open(env_path):
    if line.startswith("DATABASE_URL="):
        DB_URL = line.strip().split("=", 1)[1].strip().strip('"')
        break

conn = psycopg2.connect(DB_URL)
cur = conn.cursor(cursor_factory=RealDictCursor)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
STUB_DATE = date(1901, 1, 1)
MASTER_CSV = os.path.join(os.path.dirname(__file__), "..",
                          "documentation/wip/MASTER_INVOICE_MATCH_LIST.csv")
OUT_CSV = os.path.join(os.path.dirname(__file__), "..",
                       "documentation/wip/PAYLINE_PROPOSALS.csv")

# Period-blended mid rates to SGD (for neutralization, plausibility only)
FX_TO_SGD = {
    "SGD": 1.0, "AUD": 0.90, "USD": 1.34, "NZD": 0.83,
    "INR": 0.0161, "MYR": 0.30, "EUR": 1.45, "GBP": 1.68,
    "PHP": 0.024,
}

EXACT_THRESHOLD_SGD = 0.01    # ≤ $0.01 SGD delta
NEAR_1PCT_THRESHOLD = 0.01    # ≤ 1% of invoice amount (SGD-neutralized)
GST_LOW  = 0.085              # ±8.5%–11.5% band for GST tier
GST_HIGH = 0.115
BATCH_MAX_SIZE = 8            # max invoices in a batch group
AMOUNT_GAP_MIN_DELTA_PCT = 0.01  # must be > 1% to be AMOUNT_GAP (else NEAR_1PCT)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def to_sgd(amount, ccy: str):
    rate = FX_TO_SGD.get((ccy or "SGD").upper())
    if rate is None:
        return None
    return round(abs(float(amount)) * rate, 4)


def sgd_delta(inv_amt, inv_ccy, txn_amt, txn_ccy):
    """Return signed SGD delta (txn_sgd - inv_sgd) and absolute pct."""
    i = to_sgd(inv_amt, inv_ccy)
    t = to_sgd(txn_amt, txn_ccy)
    if i is None or t is None or float(i) == 0:
        return None, None
    delta = round(t - i, 4)
    pct = abs(delta) / float(i)
    return delta, pct


def norm(s: str) -> str:
    """Uppercase, strip non-alnum."""
    return re.sub(r"[^A-Za-z0-9]", "", (s or "")).upper()


def invoice_tokens(inv_num: str):
    """
    Return specific tokens from an invoice number:
    - full normalized number (if len >= 5)
    - any numeric run of >= 5 digits
    Never a shared prefix.
    """
    if not inv_num:
        return []
    full = norm(inv_num)
    out = []
    if len(full) >= 5:
        out.append(full)
    for run in re.findall(r"\d{5,}", inv_num):
        out.append(run)
    return list(dict.fromkeys(out))


def ref_match(tokens, txn) -> bool:
    """Return True if any token appears in txn ref/desc/raw."""
    if not tokens:
        return False
    hay = norm(" ".join([
        (txn.get("reference_number") or ""),
        (txn.get("description") or ""),
        (txn.get("original_csv_row") or ""),
    ]))
    return any(tok in hay for tok in tokens)


def classify_basis_and_confidence(
    inv_amt, inv_ccy, txn_amt, txn_ccy, ref_confirmed: bool
):
    """
    Given amounts and whether ref was confirmed, return (basis, confidence).
    Does NOT apply temporal gate (caller does that).
    """
    delta_sgd, pct = sgd_delta(inv_amt, inv_ccy, txn_amt, txn_ccy)
    if delta_sgd is None:
        # unknown FX pair — fall back to raw same-ccy comparison
        inv_f = abs(float(inv_amt))
        txn_f = abs(float(txn_amt))
        if inv_f == 0:
            return "AMOUNT_GAP", "LOW", 0.0
        raw_delta = txn_f - inv_f
        raw_pct = abs(raw_delta) / inv_f
        if raw_pct <= EXACT_THRESHOLD_SGD:
            basis = "EXACT"
        elif raw_pct <= NEAR_1PCT_THRESHOLD:
            basis = "NEAR_1PCT"
        elif GST_LOW <= raw_pct <= GST_HIGH:
            basis = "GST"
        else:
            basis = "AMOUNT_GAP"
        confidence = "MED" if ref_confirmed else "LOW"
        return basis, confidence, round(raw_delta, 4)

    abs_delta = abs(delta_sgd)
    if abs_delta <= EXACT_THRESHOLD_SGD:
        basis = "EXACT"
        confidence = "HIGH" if ref_confirmed else "MED"
    elif pct <= NEAR_1PCT_THRESHOLD:
        basis = "NEAR_1PCT"
        confidence = "HIGH" if ref_confirmed else "MED"
    elif GST_LOW <= pct <= GST_HIGH:
        basis = "GST"
        confidence = "MED" if ref_confirmed else "LOW"
    else:
        basis = "AMOUNT_GAP"
        confidence = "MED" if ref_confirmed else "LOW"

    return basis, confidence, round(delta_sgd, 4)


# ──────────────────────────────────────────────────────────────────────────────
# Step 1: Load target invoices from master list
# ──────────────────────────────────────────────────────────────────────────────
print("Loading target invoices from MASTER_INVOICE_MATCH_LIST.csv …")
with open(MASTER_CSV, newline="") as f:
    master_rows = list(csv.DictReader(f))

target_rows = [
    r for r in master_rows
    if r["provisionally_paid"] == "Y"
    and r["status"] == "UNMATCHED"
    and r["counterparty_id"]
    and not r["reason"].startswith("DUPLICATE_INVOICE")
    and not r["reason"].startswith("PAYMENT_ALREADY_CLAIMED")
]
target_ids = [int(r["invoice_id"]) for r in target_rows]
print(f"  Target invoices: {len(target_rows)}")

# ──────────────────────────────────────────────────────────────────────────────
# Step 2: Fetch invoice details from DB
# ──────────────────────────────────────────────────────────────────────────────
cur.execute("""
    SELECT id, counterparty_id, invoice_number, total_amount, currency, invoice_date,
           ai_extraction_raw->'recovery'->>'invoice_number' AS recovered_invoice_number
    FROM finance_invoices
    WHERE id = ANY(%s)
""", (target_ids,))
db_invoices = {r["id"]: r for r in cur.fetchall()}
print(f"  Fetched {len(db_invoices)} invoices from DB")

# ──────────────────────────────────────────────────────────────────────────────
# Step 3: Fetch all candidate transactions per counterparty
# (outflow, counterparty_id in our set)
# ──────────────────────────────────────────────────────────────────────────────
cp_ids = list({int(r["counterparty_id"]) for r in target_rows})
print(f"  Fetching outflow transactions for {len(cp_ids)} counterparties …")

cur.execute("""
    SELECT id, counterparty_id, transaction_date, amount, currency,
           reference_number, description, original_csv_row
    FROM finance_transactions
    WHERE counterparty_id = ANY(%s)
      AND amount < 0
    ORDER BY transaction_date
""", (cp_ids,))
all_txns = cur.fetchall()
print(f"  Fetched {len(all_txns)} outflow transactions")

# Index by counterparty_id
cp_txns = defaultdict(list)
for t in all_txns:
    cp_txns[t["counterparty_id"]].append(t)

# ──────────────────────────────────────────────────────────────────────────────
# Step 4: Build proposals — layer 1: reference + amount matching
#         for each invoice, find best candidate respecting temporal gate
# ──────────────────────────────────────────────────────────────────────────────

def best_candidate_for_invoice(inv, master_row: dict):
    """
    Returns dict with proposal fields for a single invoice.
    Respects: temporal gate (txn_date >= inv_date, skip stub-dated).
    Priority: ref+amount > ref+near > ref > amount_exact > amount_near > amount_gst > amount_gap > NONE
    Confidence uplift: EXACT with exactly 1 same-amount candidate → HIGH (skill doctrine: "exactly one candidate").
    """
    cp_id = inv["counterparty_id"]
    inv_date = inv["invoice_date"]
    inv_amt = inv["total_amount"]
    inv_ccy = inv["currency"] or "SGD"

    is_stub = inv_date is None or inv_date <= STUB_DATE

    inv_num = (inv["invoice_number"] or "").strip()
    rec_num = (inv.get("recovered_invoice_number") or "").strip()
    # Use own invoice_number, fall back to recovered
    effective_num = inv_num or rec_num
    toks = invoice_tokens(effective_num)

    txns = cp_txns.get(cp_id, [])

    # Apply temporal gate: only transactions on/after invoice_date (skip for stubs)
    if not is_stub:
        valid_txns = [t for t in txns if t["transaction_date"] >= inv_date]
    else:
        valid_txns = []  # stub-dated → can't validate temporal → NONE

    if not valid_txns:
        return _none_row(inv, master_row)

    # Evaluate each transaction
    candidates = []
    for t in valid_txns:
        r_confirmed = ref_match(toks, t) if toks else False
        delta_sgd, pct = sgd_delta(inv_amt, inv_ccy, t["amount"], t["currency"])

        candidates.append({
            "txn": t,
            "ref_confirmed": r_confirmed,
            "delta_sgd": delta_sgd,
            "pct": pct,
        })

    # Rank: ref+exact > ref+near1pct > ref+gst > ref+gap > exact > near1pct > gst > gap > NONE
    def rank_key(c):
        ref = c["ref_confirmed"]
        pct = c["pct"] if c["pct"] is not None else 99
        if ref and pct <= EXACT_THRESHOLD_SGD:         return (0, pct)
        if ref and pct <= NEAR_1PCT_THRESHOLD:         return (1, pct)
        if ref and GST_LOW <= pct <= GST_HIGH:         return (2, pct)
        if ref:                                        return (3, pct)
        if pct is not None and pct <= EXACT_THRESHOLD_SGD:   return (4, pct)
        if pct is not None and pct <= NEAR_1PCT_THRESHOLD:   return (5, pct)
        if pct is not None and GST_LOW <= pct <= GST_HIGH:   return (6, pct)
        return (7, pct if pct is not None else 99)

    candidates.sort(key=rank_key)
    best = candidates[0]
    t = best["txn"]

    basis, confidence, delta = classify_basis_and_confidence(
        inv_amt, inv_ccy, t["amount"], t["currency"], best["ref_confirmed"]
    )

    # Confidence uplift for EXACT with no ref: if exactly 1 same-amount candidate exists
    # (per skill doctrine: "exact amount + exactly one candidate → knock off, weak 9% fallback")
    if basis == "EXACT" and not best["ref_confirmed"]:
        exact_same_amt = [c for c in candidates
                          if c["pct"] is not None and c["pct"] <= EXACT_THRESHOLD_SGD]
        if len(exact_same_amt) == 1:
            confidence = "HIGH"
        # else remains MED (multiple exact-amount candidates = ambiguous)

    # Similarly NEAR_1PCT with no ref + single candidate → MED (already MED, keep)
    # ref+EXACT → HIGH already from classify_basis_and_confidence

    date_gap = (t["transaction_date"] - inv_date).days if not is_stub else ""

    return {
        "invoice_id": inv["id"],
        "counterparty_id": cp_id,
        "inv_amount": float(inv_amt),
        "inv_currency": inv_ccy,
        "invoice_date": str(inv_date) if inv_date else "",
        "proposed_txn_id": t["id"],
        "txn_amount": abs(float(t["amount"])),
        "txn_currency": t["currency"] or "",
        "txn_date": str(t["transaction_date"]),
        "match_basis": basis,
        "amount_delta_sgd": delta,
        "date_gap_days": date_gap,
        "ref_confirmed": "Y" if best["ref_confirmed"] else "N",
        "confidence": confidence,
        "group_id": "",
    }


def _none_row(inv, master_row):
    return {
        "invoice_id": inv["id"],
        "counterparty_id": inv["counterparty_id"],
        "inv_amount": float(inv["total_amount"]) if inv["total_amount"] else "",
        "inv_currency": inv["currency"] or "",
        "invoice_date": str(inv["invoice_date"]) if inv["invoice_date"] else "",
        "proposed_txn_id": "",
        "txn_amount": "",
        "txn_currency": "",
        "txn_date": "",
        "match_basis": "NONE",
        "amount_delta_sgd": "",
        "date_gap_days": "",
        "ref_confirmed": "N",
        "confidence": "",
        "group_id": "",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Step 5: Build individual proposals
# ──────────────────────────────────────────────────────────────────────────────
print("Building individual invoice proposals …")
master_by_id = {int(r["invoice_id"]): r for r in target_rows}
proposals = {}  # invoice_id -> proposal dict

for inv_id in target_ids:
    inv = db_invoices.get(inv_id)
    master_row = master_by_id[inv_id]
    if inv is None:
        # Invoice not found in DB (shouldn't happen, but guard)
        proposals[inv_id] = {
            "invoice_id": inv_id, "counterparty_id": master_row["counterparty_id"],
            "inv_amount": master_row["inv_amount"], "inv_currency": master_row["inv_currency"],
            "invoice_date": master_row["invoice_date"],
            "proposed_txn_id": "", "txn_amount": "", "txn_currency": "", "txn_date": "",
            "match_basis": "NONE", "amount_delta_sgd": "", "date_gap_days": "",
            "ref_confirmed": "N", "confidence": "", "group_id": "",
        }
    else:
        proposals[inv_id] = best_candidate_for_invoice(inv, master_row)

print(f"  Built {len(proposals)} individual proposals")

# ──────────────────────────────────────────────────────────────────────────────
# Step 6: BATCH_SUM detection
# For each counterparty, check if a single txn equals the sum of multiple
# UNMATCHED invoices from that counterparty. Only for invoices currently NONE.
# ──────────────────────────────────────────────────────────────────────────────
print("Running BATCH_SUM detection …")
none_inv_ids_by_cp = defaultdict(list)
for inv_id, prop in proposals.items():
    if prop["match_basis"] == "NONE":
        cp_id = int(prop["counterparty_id"])
        none_inv_ids_by_cp[cp_id].append(inv_id)

batch_group_counter = 0
batch_proposals = {}  # inv_id -> batch proposal (replaces NONE rows)
used_in_batch = set()  # txn_ids used in batch groups

for cp_id, inv_ids in none_inv_ids_by_cp.items():
    if len(inv_ids) < 2:
        continue

    # Fetch invoice objects for these NONE invoices
    cp_inv_objs = [db_invoices[i] for i in inv_ids if i in db_invoices]
    cp_inv_objs = [i for i in cp_inv_objs if i["total_amount"] and i["invoice_date"] and i["invoice_date"] > STUB_DATE]
    if len(cp_inv_objs) < 2:
        continue

    txns = cp_txns.get(cp_id, [])
    if not txns:
        continue

    # Try all combinations of 2..BATCH_MAX_SIZE invoices
    # For each subset, check if any single txn amount equals the sum (SGD-neutralized)
    for size in range(2, min(BATCH_MAX_SIZE + 1, len(cp_inv_objs) + 1)):
        for subset in combinations(cp_inv_objs, size):
            # All invoices in subset must have a common base currency or we go SGD
            sum_sgd = sum(to_sgd(i["total_amount"], i["currency"]) for i in subset
                          if to_sgd(i["total_amount"], i["currency"]) is not None)
            if sum_sgd == 0:
                continue

            # Temporal gate: txn_date >= latest invoice_date in subset
            max_inv_date = max(i["invoice_date"] for i in subset if i["invoice_date"])

            for t in txns:
                if t["transaction_date"] < max_inv_date:
                    continue
                txn_sgd = to_sgd(t["amount"], t["currency"])
                if txn_sgd is None:
                    continue
                delta = abs(txn_sgd - sum_sgd)
                if sum_sgd > 0 and delta / sum_sgd <= NEAR_1PCT_THRESHOLD:
                    # Match found — only if txn not already used in a batch
                    if t["id"] in used_in_batch:
                        continue
                    # Also ensure none of the subset invoices already have a non-NONE proposal
                    if any(proposals[i["id"]]["match_basis"] != "NONE" for i in subset):
                        continue

                    batch_group_counter += 1
                    group_id = f"BATCH_{cp_id}_{batch_group_counter}"
                    used_in_batch.add(t["id"])

                    for inv in subset:
                        inv_id = inv["id"]
                        inv_sgd = to_sgd(inv["total_amount"], inv["currency"])
                        share_delta = round(txn_sgd / len(subset) - inv_sgd, 4) if inv_sgd else ""
                        batch_proposals[inv_id] = {
                            "invoice_id": inv_id,
                            "counterparty_id": cp_id,
                            "inv_amount": float(inv["total_amount"]),
                            "inv_currency": inv["currency"] or "",
                            "invoice_date": str(inv["invoice_date"]),
                            "proposed_txn_id": t["id"],
                            "txn_amount": abs(float(t["amount"])),
                            "txn_currency": t["currency"] or "",
                            "txn_date": str(t["transaction_date"]),
                            "match_basis": "BATCH_SUM",
                            "amount_delta_sgd": share_delta,
                            "date_gap_days": (t["transaction_date"] - inv["invoice_date"]).days,
                            "ref_confirmed": "N",
                            "confidence": "MED",
                            "group_id": group_id,
                        }
                    break  # stop iterating txns for this subset once matched

# Apply batch proposals (only replaces NONE rows)
batch_count = 0
for inv_id, bp in batch_proposals.items():
    if proposals[inv_id]["match_basis"] == "NONE":
        proposals[inv_id] = bp
        batch_count += 1
print(f"  BATCH_SUM: {batch_group_counter} groups, {batch_count} invoice slots filled")

# ──────────────────────────────────────────────────────────────────────────────
# Step 7: Payment-uniqueness enforcement
# Each txn_id may appear at most once OUTSIDE a BATCH_SUM group.
# Within a group, multiple invoices may share the same txn.
# If two non-batch proposals claim the same txn, keep the best (lower rank basis,
# then smaller delta_sgd), leave the other as NONE.
# ──────────────────────────────────────────────────────────────────────────────
print("Enforcing payment-uniqueness …")

BASIS_ORDER = ["EXACT", "NEAR_1PCT", "GST", "BATCH_SUM", "AMOUNT_GAP", "NONE"]

def basis_rank(basis):
    try:
        return BASIS_ORDER.index(basis)
    except ValueError:
        return len(BASIS_ORDER)

# Collect non-NONE non-BATCH proposals by txn_id
txn_claimants = defaultdict(list)  # txn_id -> list of inv_ids
for inv_id, prop in proposals.items():
    if prop["match_basis"] not in ("NONE", "BATCH_SUM") and prop["proposed_txn_id"]:
        txn_claimants[prop["proposed_txn_id"]].append(inv_id)

demoted = 0
for txn_id, claimant_ids in txn_claimants.items():
    if len(claimant_ids) <= 1:
        continue
    # Sort by (basis_rank, abs(delta_sgd), date_gap_days)
    def sort_key(inv_id):
        p = proposals[inv_id]
        delta = abs(float(p["amount_delta_sgd"])) if p["amount_delta_sgd"] != "" else 9e9
        gap = p["date_gap_days"] if p["date_gap_days"] != "" else 9999
        return (basis_rank(p["match_basis"]), delta, gap)

    claimant_ids.sort(key=sort_key)
    # Keep the first (best); demote the rest to NONE
    for loser_id in claimant_ids[1:]:
        inv = db_invoices.get(loser_id)
        master_row = master_by_id[loser_id]
        proposals[loser_id] = _none_row(inv, master_row) if inv else proposals[loser_id]
        proposals[loser_id]["match_basis"] = "NONE"
        demoted += 1

print(f"  Demoted {demoted} duplicate-claim proposals to NONE")

# ──────────────────────────────────────────────────────────────────────────────
# Step 8: Validate and write output
# ──────────────────────────────────────────────────────────────────────────────
print("Validating …")
assert len(proposals) == len(target_ids), \
    f"Row count mismatch: {len(proposals)} proposals vs {len(target_ids)} targets"

# Check payment uniqueness (outside BATCH_SUM)
non_batch = [(inv_id, p["proposed_txn_id"]) for inv_id, p in proposals.items()
             if p["match_basis"] not in ("NONE", "BATCH_SUM") and p["proposed_txn_id"]]
txn_counter = defaultdict(list)
for inv_id, txn_id in non_batch:
    txn_counter[txn_id].append(inv_id)
duplicates = {t: ids for t, ids in txn_counter.items() if len(ids) > 1}
assert not duplicates, f"Payment uniqueness violated: {duplicates}"

print(f"  Validation passed: {len(proposals)} rows, 0 duplicate txn claims")

FIELDNAMES = [
    "invoice_id", "counterparty_id", "inv_amount", "inv_currency", "invoice_date",
    "proposed_txn_id", "txn_amount", "txn_currency", "txn_date",
    "match_basis", "amount_delta_sgd", "date_gap_days",
    "ref_confirmed", "confidence", "group_id",
]

with open(OUT_CSV, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    for inv_id in target_ids:
        writer.writerow(proposals[inv_id])

print(f"\nWrote {len(proposals)} rows -> {os.path.abspath(OUT_CSV)}")

# ──────────────────────────────────────────────────────────────────────────────
# Step 9: Summary
# ──────────────────────────────────────────────────────────────────────────────
from collections import Counter

basis_counts = Counter(p["match_basis"] for p in proposals.values())
conf_counts = Counter(p["confidence"] for p in proposals.values() if p["confidence"])
matched_count = sum(1 for p in proposals.values() if p["match_basis"] != "NONE")

print("\n" + "="*60)
print("PAYLINE SUMMARY")
print("="*60)
print(f"Total target invoices : {len(proposals)}")
print(f"With defensible match : {matched_count}")
print(f"No match (NONE)       : {basis_counts.get('NONE', 0)}")
print()
print("By match_basis:")
for basis in BASIS_ORDER:
    n = basis_counts.get(basis, 0)
    if n: print(f"  {basis:<15} {n}")
print()
print("By confidence:")
for conf in ["HIGH", "MED", "LOW"]:
    n = conf_counts.get(conf, 0)
    if n: print(f"  {conf:<6} {n}")
print("="*60)

conn.close()
