#!/usr/bin/env python
"""
Process base64 files staged in finance_recovery_files (populated from Retool storage by
the sandbox bridge): run OUR vision extractor, write the `recovery` block into
ai_extraction_raw (additive), flag vendor_issue / amount_issue, then delete the staged row.

Supervised prod write (POL-83): backup ai_extraction_raw first; JE/JL invariant tripwire.
Usage: vr2_recover_from_staged.py [--write]
"""
import os, re, sys, json, base64
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import psycopg2
from psycopg2.extras import RealDictCursor, Json

WRITE = "--write" in sys.argv
for l in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
    l = l.strip()
    if l and not l.startswith("#") and "=" in l:
        k, v = l.split("=", 1); os.environ.setdefault(k, v.strip().strip('"'))
DB = os.environ["DATABASE_URL"]
from src.services.ai_extraction_service import ai_extraction_service

inv_by_fid = {int(k): v for k, v in json.load(open("/tmp/recover291.json"))["invoice_by_fid"].items()}
def norm(s): return re.sub(r'[^a-z0-9]', '', (s or '').lower())
def vmatch(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb: return None
    if na == nb: return True
    if min(len(na), len(nb)) >= 6 and (na in nb or nb in na): return True
    return False
def ext_of(name, mime):
    n = (name or "").lower()
    if n.endswith(".pdf") or (mime or "") == "application/pdf": return ".pdf"
    if n.endswith((".jpg", ".jpeg")): return ".jpg"
    return ".png"

conn = psycopg2.connect(DB); cur = conn.cursor(cursor_factory=RealDictCursor)
cur.execute("SELECT count(*) je FROM finance_journal_entries"); je0 = cur.fetchone()["je"]
cur.execute("SELECT count(*) jl FROM finance_journal_lines"); jl0 = cur.fetchone()["jl"]

cur.execute("SELECT finance_db_id, name, mime, b64 FROM finance_recovery_files ORDER BY finance_db_id")
staged = cur.fetchall()
print(f"staged files: {len(staged)}")

results = []
for s in staged:
    fid = s["finance_db_id"]; iid = inv_by_fid.get(fid)
    if not iid: continue
    cur.execute("""SELECT i.total_amount, i.currency, cp.name cpname
                   FROM finance_invoices i LEFT JOIN finance_counterparties cp ON cp.id=i.counterparty_id
                   WHERE i.id=%s""", (iid,))
    row = cur.fetchone()
    if not row: continue
    try:
        b = base64.b64decode((s["b64"] or "") + "=" * (-len(s["b64"] or "") % 4))
        d = ai_extraction_service.extract_invoice_data(b, entity_names=None, file_extension=ext_of(s["name"], s["mime"]))
    except Exception as e:
        d = {"extraction_error": f"decode/extract failed: {e}"}
    ocr_vendor = d.get("vendor_name"); ocr_amt = d.get("total_amount")
    our_amt = float(row["total_amount"]) if row["total_amount"] is not None else None
    vendor_issue = (vmatch(ocr_vendor, row["cpname"]) is False) if row["cpname"] else False
    amount_issue = None
    if ocr_amt is not None and our_amt:
        try: amount_issue = abs(float(ocr_amt) - our_amt) > max(1.0, 0.02 * our_amt)
        except Exception: amount_issue = None
    blob = {"source": "retool_attachment_recovery", "file_name": s["name"], "file_mime": s["mime"],
            "ocr": {"vendor": ocr_vendor, "invoice_number": d.get("invoice_number"), "amount": ocr_amt,
                    "currency": d.get("currency"), "invoice_date": d.get("invoice_date"), "confidence": d.get("confidence")},
            "stamped_counterparty": row["cpname"], "our_amount": str(our_amt) if our_amt is not None else None,
            "our_currency": row["currency"], "vendor_issue": vendor_issue, "amount_issue": amount_issue,
            "extraction_error": d.get("extraction_error"), "recovered_at": datetime.now(timezone.utc).isoformat()}
    results.append((iid, fid, blob))
    print(f"inv[{iid}] fid={fid} ocr={str(ocr_vendor)[:26]!r} vs cp={str(row['cpname'])[:22]!r} "
          f"| v_issue={vendor_issue} a_issue={amount_issue} inv#={d.get('invoice_number')} conf={d.get('confidence')}"
          f"{' ERR' if d.get('extraction_error') else ''}")

vi = sum(1 for r in results if r[2]["vendor_issue"]); ai = sum(1 for r in results if r[2]["amount_issue"])
err = sum(1 for r in results if r[2]["extraction_error"])
print(f"\n=== {len(results)} processed | errors {err} | vendor_issue {vi} | amount_issue {ai} ===")
if not WRITE:
    print("\n[DRY-RUN] nothing written. --write to persist + clear staged rows."); sys.exit(0)

ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
bpath = os.path.join(os.path.dirname(__file__), "..", "backups", f"recovery_blob_staged_backup_{ts}.json")
ids = [r[0] for r in results]
cur.execute("SELECT id, ai_extraction_raw FROM finance_invoices WHERE id=ANY(%s)", (ids,))
json.dump({str(r["id"]): r["ai_extraction_raw"] for r in cur.fetchall()}, open(bpath, "w"), default=str)
print(f"backup -> {bpath}")
for iid, fid, blob in results:
    cur.execute("""UPDATE finance_invoices SET ai_extraction_raw =
                   jsonb_set(coalesce(ai_extraction_raw,'{}'::jsonb),'{recovery}',%s::jsonb,true) WHERE id=%s""",
                (Json(blob), iid))
    cur.execute("DELETE FROM finance_recovery_files WHERE finance_db_id=%s", (fid,))
conn.commit()
cur.execute("SELECT count(*) je FROM finance_journal_entries"); je1 = cur.fetchone()["je"]
cur.execute("SELECT count(*) jl FROM finance_journal_lines"); jl1 = cur.fetchone()["jl"]
print(f"WROTE {len(results)} recovery blocks | JE {je0}->{je1} JL {jl0}->{jl1}")
assert je1 == je0 and jl1 == jl0, "JE/JL CHANGED"
print("OK — invariant held. staged rows for processed invoices cleared.")
conn.close()
