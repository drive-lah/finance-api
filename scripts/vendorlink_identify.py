#!/usr/bin/env python3
"""
VENDORLINK — Read-only counterparty identification for unmatched provisional invoices.

Targets: provisionally_paid=true, counterparty_id IS NULL, NOT flagged duplicate, UNMATCHED.
Outputs: documentation/wip/VENDORLINK_PROPOSALS.csv

Rules:
- Vendor signal priority: rec_ocr > extraction > retool_payee > pay_to_desc
- Normalize to lowercase alphanumeric only
- EXACT: normalized match
- SUBSTR: >=6-char substring (shorter side >=6 chars)
- Short tokens (<6 chars normalized): EXACT only, never SUBSTR
- Same-entity preference; ambiguous multi-match -> NONE
- No existing counterparty -> NEW (propose creation)
- No vendor signal -> NONE
"""

import csv
import os
import re
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.parent
MASTER_CSV = BASE_DIR / "documentation/wip/MASTER_INVOICE_MATCH_LIST.csv"
OUTPUT_CSV = BASE_DIR / "documentation/wip/VENDORLINK_PROPOSALS.csv"
DATABASE_URL = "postgresql://collectionsagent:collectionsagent@collections-db.compunokr5xr.ap-southeast-2.rds.amazonaws.com:5432/collections-db?gssencmode=disable"

PAY_TO_RE = re.compile(
    r'pay\s+to\s*[:\-]\s*([^\n\r]+)',
    re.IGNORECASE
)


def normalize(s: str) -> str:
    """Lowercase alphanumeric only — strips spaces and punctuation."""
    if not s:
        return ""
    return re.sub(r'[^a-z0-9]', '', s.lower())


def best_vendor_from_row(row: dict) -> tuple[str, str]:
    """
    Extract best vendor string from ai_extraction_raw fields.
    Returns (vendor_string, source_signal).
    All four signals are already fetched as columns in the query.
    """
    # Priority 1: recovered OCR vendor
    v = (row.get("rec_ocr_vendor") or "").strip()
    if v:
        return v, "rec_ocr"

    # Priority 2: extraction vendor_name
    v = (row.get("ext_vendor_name") or "").strip()
    if v:
        return v, "extraction"

    # Priority 3: retool_ref payee
    v = (row.get("retool_payee") or "").strip()
    if v:
        return v, "retool_payee"

    # Priority 4: parse "Pay to:" from retool_ref description
    desc = (row.get("retool_desc") or "").strip()
    if desc:
        m = PAY_TO_RE.search(desc)
        if m:
            v = m.group(1).strip()
            if v:
                return v, "pay_to_desc"

    return "", ""


def match_vendor(vendor_str: str, entity_id, counterparties: list[dict]) -> tuple:
    """
    Match vendor string against counterparties using strict rules.

    Returns:
        (match_type, proposed_cp_id, proposed_cp_name, confidence)
    """
    norm_v = normalize(vendor_str)
    if not norm_v:
        return "NONE", "", "", "LOW"

    v_len = len(norm_v)

    # Gather candidates: try same entity first, then all entities
    exact_same = []
    exact_any = []
    substr_same = []
    substr_any = []

    for cp in counterparties:
        norm_cp = normalize(cp["name"])
        if not norm_cp:
            continue
        cp_len = len(norm_cp)

        # EXACT
        if norm_v == norm_cp:
            if cp["entity_id"] == entity_id:
                exact_same.append(cp)
            else:
                exact_any.append(cp)
        # SUBSTR — only if shorter side >= 6 chars
        elif v_len >= 6 and cp_len >= 6:
            shorter = min(v_len, cp_len)
            if shorter >= 6:
                if norm_v in norm_cp or norm_cp in norm_v:
                    if cp["entity_id"] == entity_id:
                        substr_same.append(cp)
                    else:
                        substr_any.append(cp)

    # Decision: prefer same-entity exact > cross-entity exact > same-entity substr > cross-entity substr
    # Ambiguous (>1 distinct counterparty) -> NONE

    def pick(candidates, match_type, same_entity):
        unique_ids = list({c["id"] for c in candidates})
        if len(unique_ids) == 1:
            cp = candidates[0]
            if match_type == "EXACT":
                confidence = "HIGH" if same_entity else "MED"
            else:
                confidence = "MED"
            return match_type, str(cp["id"]), cp["name"], confidence
        elif len(unique_ids) > 1:
            # Ambiguous — don't guess
            return None, None, None, None
        return None, None, None, None

    # Try each tier
    for candidates, match_type, same_entity in [
        (exact_same, "EXACT", True),
        (exact_any, "EXACT", False),
        (substr_same, "SUBSTR", True),
        (substr_any, "SUBSTR", False),
    ]:
        if candidates:
            mt, cid, cname, conf = pick(candidates, match_type, same_entity)
            if mt:
                return mt, cid, cname, conf
            else:
                # Ambiguous at this tier — fall through to check if a tighter tier resolves it
                # Actually for ambiguous exact matches, give up entirely (don't fall to substr)
                if match_type == "EXACT":
                    return "NONE", "", "", "LOW"
                # For ambiguous substr, fall through to next tier
                continue

    # No match found — propose NEW
    # Confidence: MED if vendor string looks clean (>=6 chars normalized), LOW if short/ambiguous
    if v_len >= 6:
        confidence = "MED"
    else:
        confidence = "LOW"
    return "NEW", "", vendor_str, confidence


def load_target_invoice_ids() -> set[str]:
    """Load target invoice_ids from the master CSV."""
    targets = set()
    with open(MASTER_CSV) as f:
        for row in csv.DictReader(f):
            if (
                row["provisionally_paid"] == "Y"
                and not row["counterparty_id"]
                and row["status"] == "UNMATCHED"
                and not row["reason"].startswith("DUPLICATE_INVOICE")
            ):
                targets.add(row["invoice_id"])
    return targets


def main():
    print("VENDORLINK: Loading target invoice IDs from master CSV...")
    target_ids = load_target_invoice_ids()
    print(f"  Target count from master CSV: {len(target_ids)}")

    print("VENDORLINK: Connecting to DB (read-only)...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.set_session(readonly=True)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Load counterparties
    print("VENDORLINK: Loading counterparties...")
    cur.execute("SELECT id, name, entity_id FROM finance_counterparties ORDER BY id")
    counterparties = [dict(r) for r in cur.fetchall()]
    print(f"  Loaded {len(counterparties)} counterparties")

    # Load target invoices with all vendor signals
    print("VENDORLINK: Querying target invoices...")
    cur.execute("""
        SELECT
            id,
            entity_id,
            -- Signal 1: recovered OCR vendor
            ai_extraction_raw->'recovery'->'ocr'->>'vendor'          AS rec_ocr_vendor,
            -- Signal 2: extraction vendor_name
            ai_extraction_raw->'extraction'->>'vendor_name'           AS ext_vendor_name,
            -- Signal 3: retool_ref payee
            ai_extraction_raw->'retool_ref'->>'payee'                 AS retool_payee,
            -- Signal 4: retool_ref description (for Pay to: parsing)
            ai_extraction_raw->'retool_ref'->>'description'           AS retool_desc,
            -- Dedup flag to double-check
            (ai_extraction_raw->'recon'->'duplicate'->>'is_duplicate') AS is_duplicate,
            ai_extraction_raw->'provisional_paid'->>'is_provisional_paid' AS is_provisional
        FROM finance_invoices
        WHERE
            (ai_extraction_raw->'provisional_paid'->>'is_provisional_paid') = 'true'
            AND counterparty_id IS NULL
            AND (
                ai_extraction_raw->'recon'->'duplicate'->>'is_duplicate'
            ) IS DISTINCT FROM 'true'
        ORDER BY id
    """)
    db_invoices = [dict(r) for r in cur.fetchall()]
    print(f"  DB returned {len(db_invoices)} invoices")

    # Intersect with master CSV target set
    db_by_id = {str(r["id"]): r for r in db_invoices}
    target_rows = []
    for inv_id in sorted(target_ids, key=lambda x: int(x)):
        if inv_id in db_by_id:
            target_rows.append(db_by_id[inv_id])
        else:
            print(f"  WARNING: invoice_id {inv_id} in master CSV but not in DB query result")

    # Check for DB invoices not in master (info only)
    db_ids_not_in_master = set(db_by_id.keys()) - target_ids
    if db_ids_not_in_master:
        print(f"  INFO: {len(db_ids_not_in_master)} DB invoices not in master target set (skipped)")

    print(f"  Processing {len(target_rows)} invoices")

    # Process each invoice
    results = []
    stats = {"EXACT": 0, "SUBSTR": 0, "NEW": 0, "NONE": 0}
    conf_stats = {"HIGH": 0, "MED": 0, "LOW": 0}

    for row in target_rows:
        invoice_id = str(row["id"])
        entity_id = row["entity_id"]

        vendor_str, source_signal = best_vendor_from_row(row)

        if not vendor_str:
            match_type = "NONE"
            proposed_cp_id = ""
            proposed_cp_name = ""
            confidence = "LOW"
        else:
            match_type, proposed_cp_id, proposed_cp_name, confidence = match_vendor(
                vendor_str, entity_id, counterparties
            )

        stats[match_type] += 1
        conf_stats[confidence] += 1

        results.append({
            "invoice_id": invoice_id,
            "entity_id": entity_id or "",
            "best_vendor_string": vendor_str,
            "source_signal": source_signal,
            "match_type": match_type,
            "proposed_counterparty_id": proposed_cp_id,
            "proposed_counterparty_name": proposed_cp_name,
            "confidence": confidence,
        })

    cur.close()
    conn.close()

    # Write CSV
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "invoice_id", "entity_id", "best_vendor_string", "source_signal",
        "match_type", "proposed_counterparty_id", "proposed_counterparty_name", "confidence"
    ]
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # Verify row count
    with open(OUTPUT_CSV) as f:
        written_rows = sum(1 for _ in csv.DictReader(f))

    print()
    print("=" * 60)
    print("VENDORLINK RESULTS")
    print("=" * 60)
    print(f"Output: {OUTPUT_CSV.resolve()}")
    print(f"Total targets: {len(target_ids)}")
    print(f"CSV rows written: {written_rows}")
    if written_rows != len(target_ids):
        print(f"  WARNING: row count mismatch! Expected {len(target_ids)}, got {written_rows}")
    else:
        print("  Row count verified OK")
    print()
    print("Match type breakdown:")
    print(f"  EXACT (linkable to existing):  {stats['EXACT']}")
    print(f"  SUBSTR (linkable to existing): {stats['SUBSTR']}")
    print(f"  NEW (propose new counterparty):{stats['NEW']}")
    print(f"  NONE (unidentifiable):         {stats['NONE']}")
    print()
    print("Confidence breakdown:")
    for c in ["HIGH", "MED", "LOW"]:
        print(f"  {c}: {conf_stats[c]}")
    print()
    linkable = stats["EXACT"] + stats["SUBSTR"]
    print(f"Linkable to existing: {linkable}")
    print(f"Need new counterparty: {stats['NEW']}")
    print(f"Unidentifiable: {stats['NONE']}")
    print("=" * 60)

    return written_rows == len(target_ids)


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
