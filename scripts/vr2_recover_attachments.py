#!/usr/bin/env python
"""
Attachment recovery: pull the invoice file that lives in Retool storage (which our
backfill never fetched), run it through OUR existing vision extractor, and record the
result as a temporary `recovery` block inside ai_extraction_raw (additive JSON merge).

Flags (Gaurav): vendor_issue when OCR vendor disagrees with the stamped counterparty;
amount_issue when OCR amount disagrees with our invoice amount. Never overwrites the
counterparty — only writes the recovery note for review.

Supervised prod write (POL-83): backup ai_extraction_raw first, JE/JL invariant is the
tripwire (draft invoices → no JE), reversible (recovery block can be dropped).

Usage: vr2_recover_attachments.py <url_map.json>  [--write]
  url_map.json: { "<finance_db_id>": "<https url>", ... }
"""
import os, re, sys, json, urllib.request
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import psycopg2
from psycopg2.extras import RealDictCursor, Json

URLMAP = sys.argv[1]
WRITE = "--write" in sys.argv

for l in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
    l = l.strip()
    if l and not l.startswith("#") and "=" in l:
        k, v = l.split("=", 1); os.environ.setdefault(k, v.strip().strip('"'))
DB = os.environ["DATABASE_URL"]
from src.services.ai_extraction_service import ai_extraction_service

url_map = {int(k): v for k, v in json.load(open(URLMAP)).items()}
inv_by_fid = {int(k): v for k, v in json.load(open("/tmp/recover291.json"))["invoice_by_fid"].items()}

def norm(s): return re.sub(r'[^a-z0-9]', '', (s or '').lower())
def vendor_matches(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb: return None            # can't judge
    if na == nb: return True
    if min(len(na), len(nb)) >= 6 and (na in nb or nb in na): return True
    return False
def ext_of(url):
    path = url.split("?")[0].lower()
    for e in (".pdf", ".png", ".jpg", ".jpeg"):
        if path.endswith(e): return ".jpg" if e == ".jpeg" else e
    return ".png"

conn = psycopg2.connect(DB); cur = conn.cursor(cursor_factory=RealDictCursor)
cur.execute("SELECT count(*) je FROM finance_journal_entries"); je0 = cur.fetchone()["je"]
cur.execute("SELECT count(*) jl FROM finance_journal_lines"); jl0 = cur.fetchone()["jl"]

results = []
for fid, url in url_map.items():
    iid = inv_by_fid.get(fid)
    if not iid: continue
    cur.execute("""SELECT i.total_amount, i.currency, i.counterparty_id, cp.name cpname
                   FROM finance_invoices i LEFT JOIN finance_counterparties cp ON cp.id=i.counterparty_id
                   WHERE i.id=%s""", (iid,))
    row = cur.fetchone()
    if not row: continue
    try:
        b = urllib.request.urlopen(url, timeout=45).read()
        d = ai_extraction_service.extract_invoice_data(b, entity_names=None, file_extension=ext_of(url))
    except Exception as e:
        d = {"extraction_error": f"fetch/extract failed: {e}"}
    ocr_vendor = d.get("vendor_name"); ocr_amt = d.get("total_amount")
    our_amt = float(row["total_amount"]) if row["total_amount"] is not None else None
    vmatch = vendor_matches(ocr_vendor, row["cpname"]) if row["cpname"] else None
    vendor_issue = (vmatch is False)
    amount_issue = None
    if ocr_amt is not None and our_amt:
        try:
            amount_issue = abs(float(ocr_amt) - our_amt) > max(1.0, 0.02 * our_amt)
        except Exception:
            amount_issue = None
    blob = {
        "source": "retool_attachment_recovery",
        "file_url": url,
        "ocr": {"vendor": ocr_vendor, "invoice_number": d.get("invoice_number"),
                "amount": ocr_amt, "currency": d.get("currency"),
                "invoice_date": d.get("invoice_date"), "confidence": d.get("confidence")},
        "stamped_counterparty": row["cpname"],
        "our_amount": str(our_amt) if our_amt is not None else None,
        "our_currency": row["currency"],
        "vendor_issue": vendor_issue,
        "amount_issue": amount_issue,
        "extraction_error": d.get("extraction_error"),
        "recovered_at": datetime.now(timezone.utc).isoformat(),
    }
    results.append((iid, fid, blob))
    print(f"inv[{iid}] fid={fid} ocr_vendor={str(ocr_vendor)[:28]!r} vs cp={str(row['cpname'])[:24]!r} "
          f"| vendor_issue={vendor_issue} amt_issue={amount_issue} inv#={d.get('invoice_number')} "
          f"conf={d.get('confidence')}{' ERR' if d.get('extraction_error') else ''}")

ok = [r for r in results if not r[2]["extraction_error"]]
vi = sum(1 for r in results if r[2]["vendor_issue"])
ai = sum(1 for r in results if r[2]["amount_issue"])
err = len(results) - len(ok)
print(f"\n=== {len(results)} processed | extracted OK {len(ok)} | errors {err} "
      f"| vendor_issue {vi} | amount_issue {ai} ===")

if not WRITE:
    print("\n[DRY-RUN] nothing written. Re-run with --write to persist recovery blocks.")
    sys.exit(0)

ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
bpath = os.path.join(os.path.dirname(__file__), "..", "backups", f"recovery_blob_backup_{ts}.json")
ids = [r[0] for r in results]
cur.execute("SELECT id, ai_extraction_raw FROM finance_invoices WHERE id=ANY(%s)", (ids,))
json.dump({str(r["id"]): r["ai_extraction_raw"] for r in cur.fetchall()},
          open(bpath, "w"), default=str)
print(f"backup -> {bpath}")

for iid, fid, blob in results:
    cur.execute("""UPDATE finance_invoices
                   SET ai_extraction_raw = jsonb_set(coalesce(ai_extraction_raw,'{}'::jsonb),
                       '{recovery}', %s::jsonb, true)
                   WHERE id=%s""", (Json(blob), iid))
conn.commit()

cur.execute("SELECT count(*) je FROM finance_journal_entries"); je1 = cur.fetchone()["je"]
cur.execute("SELECT count(*) jl FROM finance_journal_lines"); jl1 = cur.fetchone()["jl"]
print(f"WROTE recovery block on {len(results)} invoices | JE {je0}->{je1} JL {jl0}->{jl1}")
assert je1 == je0 and jl1 == jl0, "JE/JL CHANGED"
print("OK — invariant held.")
conn.close()
