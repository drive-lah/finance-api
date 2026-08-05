#!/usr/bin/env python
"""
THE master invoice list — ONE row per invoice, all 1,947, one authoritative status.

Each invoice is exactly one of:
  MATCHED   -> carries counterparty, payment txn_id/amount/currency/date, match_class
  UNMATCHED -> carries a single clear `reason` code

Read-only. No JEs. Regenerate anytime — this file is the single source of truth.
Output: documentation/wip/MASTER_INVOICE_MATCH_LIST.csv
"""
import os, re, csv
from datetime import date
import psycopg2
from psycopg2.extras import RealDictCursor

STUB = date(1901, 1, 1)
FX_TO_SGD = {"SGD":1.0,"USD":1.34,"AUD":0.90,"NZD":0.83,"INR":0.0161,"MYR":0.30,"EUR":1.45,"GBP":1.68}

def norm(s): return re.sub(r'[^A-Za-z0-9]', '', (s or '')).upper()
def tokens(s):
    if not s: return []
    full = norm(s); out = []
    if len(full) >= 5: out.append(full)
    out += re.findall(r'\d{5,}', s)
    return list(dict.fromkeys(out))
def to_sgd(a, c):
    r = FX_TO_SGD.get((c or "SGD").upper()); return abs(float(a))*r if r else None
def neutral_flag(inv_a, inv_c, txn_a, txn_c):
    i, t = to_sgd(inv_a, inv_c), to_sgd(txn_a, txn_c)
    if not i: return 9e9, "NO_RATE"
    pct = (t - i)/i*100; same = (inv_c or "").upper()==(txn_c or "").upper()
    if abs(pct) <= 1.0: f = "TIE"
    elif abs(pct) <= 6.0: f = "TIE_FX" if not same else "NEAR"
    elif pct > 6.0: f = "TXN_GT"
    else: f = "TXN_LT"
    return pct, f

DB=None
for l in open(os.path.join(os.path.dirname(__file__),"..",".env")):
    if l.startswith("DATABASE_URL="): DB=l.strip().split("=",1)[1].strip().strip('"'); break
conn = psycopg2.connect(DB); cur = conn.cursor(cursor_factory=RealDictCursor)

cur.execute("""
  SELECT id, counterparty_id, invoice_number, total_amount, currency, invoice_date,
    (ai_extraction_raw->'provisional_paid'->>'is_provisional_paid'='true') AS prov,
    pdf_s3_key IS NOT NULL AS has_doc,
    (ai_extraction_raw->'recon'->'duplicate'->>'is_duplicate'='true') AS is_dup,
    ai_extraction_raw->'recon'->'duplicate'->>'duplicate_of' AS dup_of,
    ai_extraction_raw->'retool_ref'->>'finance_db_id' AS retool_id,
    contra_account_code AS inv_coa,
    nullif(trim(ai_extraction_raw->'extraction'->>'vendor_name'),'') AS vname,
    nullif(trim(ai_extraction_raw->'retool_ref'->>'payee'),'') AS payee,
    ai_extraction_raw->'recovery' AS recovery,
    ai_extraction_raw->'recon'->>'vendorlink_disposition' AS vl_disp
  FROM finance_invoices ORDER BY id
""")
invs = cur.fetchall()

# counterparty default COA (fallback when the invoice carries none)
cur.execute("SELECT id, default_account_code FROM finance_counterparties")
CP_DEFAULT_COA = {r["id"]: r["default_account_code"] for r in cur.fetchall()}
def resolve_coa(inv):
    if inv["inv_coa"]:
        return inv["inv_coa"], "INVOICE"
    d = CP_DEFAULT_COA.get(inv["counterparty_id"])
    if d:
        return d, "COUNTERPARTY"
    return "", "RULES_NEEDED"

# cache each counterparty's outflow txns once
cp_cache = {}
def cp_outflows(cp):
    if cp not in cp_cache:
        cur.execute("""SELECT id, transaction_date, amount, currency, reference_number,
                              description, original_csv_row
                       FROM finance_transactions WHERE counterparty_id=%s AND amount<0""",(cp,))
        cp_cache[cp] = cur.fetchall()
    return cp_cache[cp]

# advisory proposal overlays from the two agents (read-only columns; nothing booked)
def _load_props(path):
    d = {}
    if os.path.exists(path):
        for row in csv.DictReader(open(path, encoding="utf-8", errors="replace")):
            try: d[int(row["invoice_id"])] = row
            except (ValueError, TypeError): pass
    return d
PAYLINE = _load_props(os.path.join(os.path.dirname(__file__), "..", "documentation/wip/PAYLINE_PROPOSALS.csv"))
VENDORLINK = _load_props(os.path.join(os.path.dirname(__file__), "..", "documentation/wip/VENDORLINK_PROPOSALS.csv"))

rows = []
for inv in invs:
    coa, coa_source = resolve_coa(inv)
    pl = PAYLINE.get(inv["id"], {}); vl = VENDORLINK.get(inv["id"], {})
    # --- recovery block (from Retool-storage attachment OCR) ---
    rec = inv["recovery"] or {}
    rec_ocr = rec.get("ocr") or {}
    rec_err = rec.get("extraction_error")
    rec_invnum = (rec_ocr.get("invoice_number") or "").strip() if rec_ocr.get("invoice_number") else ""
    rec_vendor = rec_ocr.get("vendor") or ""
    rec_amount = rec_ocr.get("amount")
    rec_ok = bool(rec) and not rec_err and (rec_vendor or rec_invnum)
    if not rec:
        recovery_status = ""
    elif rec_err or not (rec_vendor or rec_invnum):
        recovery_status = "UNREADABLE"
    else:
        recovery_status = "RECOVERED"
    resolved = bool(rec.get("resolution"))   # 'retool_win' etc → conflict settled
    conflict = []
    if not resolved:
        if rec.get("vendor_issue") is True: conflict.append("VENDOR")
        if rec.get("amount_issue") is True: conflict.append("AMOUNT")
    conflict = "+".join(conflict)
    # effective invoice number: prefer the invoice's own, else the recovered one
    own_invnum = (inv["invoice_number"] or "").strip()
    eff_invnum = own_invnum or (rec_invnum if rec_ok else "")
    doc_effective = inv["has_doc"] or rec_ok

    r = {"invoice_id": inv["id"], "retool_id": inv["retool_id"] or "",
         "invoice_number": own_invnum,
         "counterparty_id": inv["counterparty_id"], "inv_currency": inv["currency"],
         "inv_amount": str(inv["total_amount"]) if inv["total_amount"] is not None else "",
         "invoice_date": inv["invoice_date"],
         "provisionally_paid": "Y" if inv["prov"] else "N",
         "coa": coa, "coa_source": coa_source,
         "recovery_status": recovery_status, "rec_vendor": rec_vendor,
         "rec_invoice_number": rec_invnum,
         "rec_amount": str(rec_amount) if rec_amount is not None else "",
         "conflict": conflict,
         "payline_txn": (pl.get("proposed_txn_id","") if pl.get("match_basis") not in ("NONE","",None) else ""),
         "payline_basis": pl.get("match_basis",""),
         "payline_conf": pl.get("confidence",""),
         "vendorlink_type": vl.get("match_type",""),
         "vendorlink_proposal": vl.get("proposed_counterparty_name",""),
         "status": "UNMATCHED", "match_class": "",
         "payment_txn_id": "", "payment_amount": "", "payment_currency": "",
         "payment_date": "", "reason": "", "detail_reason": ""}

    # --- funnel gates (first failing reason) ---
    if inv["is_dup"]:
        r["reason"] = f"DUPLICATE_INVOICE (of {inv['dup_of']})"; rows.append(r); continue
    if not inv["prov"]:
        r["reason"] = "NOT_PROVISIONAL_PAID"; rows.append(r); continue
    if inv["counterparty_id"] is None:
        # operator-ruled exclusions (VENDORLINK remarks) take precedence
        if inv["vl_disp"] in ("not_a_vendor", "internal_own", "do_not_create"):
            r["reason"] = "EXCLUDED_" + inv["vl_disp"].upper(); rows.append(r); continue
        if inv["vl_disp"] == "hold_review":
            r["reason"] = "NO_CP_HOLD_REVIEW"; rows.append(r); continue
        if inv["vl_disp"] == "human_review":
            r["reason"] = "HUMAN_REVIEW_IDENTIFY_VENDOR"; rows.append(r); continue
        # split: vendor was read (extraction/payee) but no existing counterparty matched,
        # vs nothing readable (no doc / extraction failed / no payee)
        if inv["vname"] or inv["payee"] or (rec_ok and rec_vendor):
            r["reason"] = "NO_CP_VENDOR_READ_NO_MATCH"   # vendor read (extraction/payee/recovery), no existing cp -> LINKABLE
        elif inv["has_doc"] or rec_ok:
            r["reason"] = "NO_CP_HAS_DOC_NO_VENDOR"      # doc present but no vendor extracted
        else:
            r["reason"] = "NO_CP_NO_DOC"                 # genuinely no document
        rows.append(r); continue
    if inv["total_amount"] is None or float(inv["total_amount"]) <= 0:
        r["reason"] = "NO_AMOUNT"; rows.append(r); continue
    if not doc_effective:
        r["reason"] = "NO_DOCUMENT_UNREADABLE" if recovery_status == "UNREADABLE" else "NO_DOCUMENT"
        rows.append(r); continue
    if not eff_invnum:
        r["reason"] = "NO_INVOICE_NUMBER"; rows.append(r); continue
    toks = tokens(eff_invnum)
    if not toks:
        r["reason"] = "INVOICE_NUMBER_TOO_SHORT"; rows.append(r); continue

    # --- reference match against counterparty outflows ---
    inv_dated = inv["invoice_date"] is not None and inv["invoice_date"] > STUB
    cands = []
    for t in cp_outflows(inv["counterparty_id"]):
        hay = norm(" ".join([t["reference_number"] or "", t["description"] or "", t["original_csv_row"] or ""]))
        if any(tok in hay for tok in toks):
            temporal_ok = (t["transaction_date"] >= inv["invoice_date"]) if inv_dated else None
            cands.append((t, temporal_ok))
    if not cands:
        r["reason"] = "NO_REFERENCE_MATCH"; rows.append(r); continue

    valid = [c for c in cands if c[1] is not False]
    if not valid:
        r["reason"] = "TEMPORAL_BLOCK (payment predates invoice)"; rows.append(r); continue

    # amount-closest among temporally-valid
    def apct(c): return abs(neutral_flag(inv["total_amount"], inv["currency"], c[0]["amount"], c[0]["currency"])[0])
    best, _ = min(valid, key=apct)
    pct, flag = neutral_flag(inv["total_amount"], inv["currency"], best["amount"], best["currency"])
    if flag in ("TIE", "TIE_FX", "NEAR"):
        r["status"] = "MATCHED"; r["match_class"] = "FIRST_CLASS_REF"
        r["payment_txn_id"] = best["id"]
        r["payment_amount"] = str(abs(float(best["amount"])))
        r["payment_currency"] = best["currency"]
        r["payment_date"] = best["transaction_date"]
        r["reason"] = "reference+amount+temporal"
    else:
        r["reason"] = f"AMOUNT_GAP ({flag}, {pct:.0f}% in SGD)"
        # keep the candidate visible for review without marking matched
        r["payment_txn_id"] = best["id"]; r["payment_amount"] = str(abs(float(best["amount"])))
        r["payment_currency"] = best["currency"]; r["payment_date"] = best["transaction_date"]
    rows.append(r)

# --- payment-uniqueness: one payment -> one invoice ---
by_txn = {}
for r in rows:
    if r["status"] == "MATCHED":
        by_txn.setdefault(r["payment_txn_id"], []).append(r)
for txn, claimers in by_txn.items():
    if len(claimers) > 1:
        # keep the exact-amount / closest one; demote the rest
        def keyf(r):
            return abs(float(r["payment_amount"]) - abs(float(r["inv_amount"])))
        keep = min(claimers, key=keyf)
        for r in claimers:
            if r is not keep:
                r["status"] = "UNMATCHED"; r["match_class"] = ""
                r["reason"] = f"PAYMENT_ALREADY_CLAIMED (txn {txn} -> inv {keep['invoice_id']})"
                for k in ("payment_txn_id","payment_amount","payment_currency","payment_date"):
                    r[k] = ""

# --- ACCEPT PAYLINE HIGH/MED proposals as MATCHED (Gaurav 2026-08-03) ---
# global payment-uniqueness: a txn already claimed by a reference match (or an earlier
# accepted payline) cannot be reused.
used_txn = set(str(r["payment_txn_id"]) for r in rows if r["status"] == "MATCHED" and r["payment_txn_id"])
accepted = 0
for r in rows:
    if r["status"] == "MATCHED":
        continue
    if r.get("payline_conf") in ("HIGH", "MED") and r.get("payline_txn"):
        txn = str(r["payline_txn"])
        if txn in used_txn:
            r["reason"] = f"PAYLINE_TXN_TAKEN ({txn})"; continue
        pl = PAYLINE.get(r["invoice_id"], {})
        used_txn.add(txn)
        r["status"] = "MATCHED"
        r["match_class"] = "PAYLINE_" + (r.get("payline_basis") or "")
        r["payment_txn_id"] = txn
        r["payment_amount"] = pl.get("txn_amount", "")
        r["payment_currency"] = pl.get("txn_currency", "")
        r["payment_date"] = pl.get("txn_date", "")
        r["reason"] = f"payline amount+date accepted ({r['payline_conf']})"
        accepted += 1
print(f"PAYLINE HIGH/MED accepted as MATCHED: {accepted}")

# --- collapse "paid + vendor known + still unmatched" into ONE human-review category (Gaurav 2026-08-03) ---
npf = 0
for r in rows:
    if (r["status"] == "UNMATCHED" and r["provisionally_paid"] == "Y" and r["counterparty_id"]
            and not r["reason"].startswith("DUPLICATE")):
        r["detail_reason"] = r["reason"]      # preserve the granular blocker
        r["reason"] = "NO_MATCHING_PAYMENT_FOUND"
        npf += 1
print(f"NO_MATCHING_PAYMENT_FOUND (paid, vendor known, unmatched): {npf}")

out = os.path.join(os.path.dirname(__file__),"..","documentation/wip/MASTER_INVOICE_MATCH_LIST.csv")
with open(out,"w",newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader()
    for r in rows: w.writerow(r)

# summary
import collections
print(f"TOTAL invoices: {len(rows)}")
matched = [r for r in rows if r["status"]=="MATCHED"]
print(f"MATCHED (first-class): {len(matched)}")
print("UNMATCHED reasons:")
rc = collections.Counter(r["reason"].split(" (")[0] for r in rows if r["status"]=="UNMATCHED")
for k,v in rc.most_common(): print(f"  {k:32s}: {v}")
assert len(matched) == len(set(str(r["payment_txn_id"]) for r in matched)), "payment not unique!"
print("payment-uniqueness: OK (every matched payment used once)")
print("COA source:")
for k,v in collections.Counter(r["coa_source"] for r in rows).most_common():
    print(f"  {k:16s}: {v}")
print("\nRECOVERY (attachment OCR from Retool storage):")
for k,v in collections.Counter(r["recovery_status"] or "(none)" for r in rows).most_common():
    print(f"  {k:12s}: {v}")
print("CONFLICTS to resolve (recovered doc disagrees with our data):")
cf = collections.Counter(r["conflict"] for r in rows if r["conflict"])
for k,v in cf.most_common(): print(f"  {k:14s}: {v}")
print(f"  TOTAL conflicts: {sum(cf.values())}")
print(f"\n-> {out}")
conn.close()
