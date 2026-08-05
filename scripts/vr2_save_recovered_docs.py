#!/usr/bin/env python
"""
Persist recovered invoice files into OUR S3 and set pdf_s3_key + pdf_content_hash, so the
invoice properly "has a document". NARROW by design: does NOT touch counterparty / amount /
invoice_number (conflicts stay flagged in ai_extraction_raw.recovery for operator review).

Two sources:
  --url     : recovered invoices whose recovery.file_url is set (the 68 public-S3 ones) -> fetch + upload
  --staged  : recovered invoices with base64 in finance_recovery_files (re-pulled UUIDs) -> upload

Supervised prod write (POL-83): backup (id, pdf_s3_key, hash) first; JE/JL invariant tripwire.
Default is DRY-RUN; add --write to persist.
"""
import os, sys, json, hashlib, base64, urllib.request
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import psycopg2
from psycopg2.extras import RealDictCursor

MODE = "staged" if "--staged" in sys.argv else "url"
WRITE = "--write" in sys.argv
for l in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
    l = l.strip()
    if l and not l.startswith("#") and "=" in l:
        k, v = l.split("=", 1); os.environ.setdefault(k, v.strip().strip('"'))
DB = os.environ["DATABASE_URL"]
from src.services.s3_service import s3_service
assert s3_service.is_configured(), "S3 not configured in .env"

conn = psycopg2.connect(DB); cur = conn.cursor(cursor_factory=RealDictCursor)
cur.execute("SELECT count(*) je FROM finance_journal_entries"); je0 = cur.fetchone()["je"]
cur.execute("SELECT count(*) jl FROM finance_journal_lines"); jl0 = cur.fetchone()["jl"]

def fname_from_url(u):
    return urllib.parse.unquote(u.split("?")[0].rsplit("/", 1)[-1]) if u else "recovered_invoice"
import urllib.parse

work = []  # (invoice_id, bytes, filename, entity_id)
if MODE == "url":
    cur.execute("""SELECT id, entity_id, ai_extraction_raw->'recovery'->>'file_url' url
                   FROM finance_invoices
                   WHERE ai_extraction_raw ? 'recovery'
                     AND ai_extraction_raw->'recovery'->>'file_url' IS NOT NULL
                     AND pdf_s3_key IS NULL""")
    for r in cur.fetchall():
        try:
            b = urllib.request.urlopen(r["url"], timeout=45).read()
            work.append((r["id"], b, fname_from_url(r["url"]), r["entity_id"]))
        except Exception as e:
            print(f"  fetch FAIL inv[{r['id']}]: {e}")
else:
    cur.execute("""SELECT f.finance_db_id, f.name, f.b64, i.id inv_id, i.entity_id
                   FROM finance_recovery_files f
                   JOIN finance_invoices i ON (i.ai_extraction_raw->'retool_ref'->>'finance_db_id')=f.finance_db_id::text
                   WHERE i.pdf_s3_key IS NULL""")
    for r in cur.fetchall():
        try:
            b = base64.b64decode((r["b64"] or "") + "=" * (-len(r["b64"] or "") % 4))
            work.append((r["inv_id"], b, r["name"] or "recovered_invoice", r["entity_id"]))
        except Exception as e:
            print(f"  decode FAIL fid[{r['finance_db_id']}]: {e}")

print(f"MODE={MODE} | files to upload: {len(work)}")
if not WRITE:
    for iid, b, fn, ent in work[:5]:
        print(f"  would upload inv[{iid}] {fn} {len(b)}B entity={ent}")
    print("[DRY-RUN] add --write to upload + set pdf_s3_key."); sys.exit(0)

ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
bpath = os.path.join(os.path.dirname(__file__), "..", "backups", f"recovered_pdf_key_backup_{MODE}_{ts}.json")
ids = [w[0] for w in work]
cur.execute("SELECT id, pdf_s3_key, pdf_content_hash FROM finance_invoices WHERE id=ANY(%s)", (ids,))
json.dump({str(r["id"]): {"pdf_s3_key": r["pdf_s3_key"], "pdf_content_hash": r["pdf_content_hash"]}
           for r in cur.fetchall()}, open(bpath, "w"))
print(f"backup -> {bpath}")

done = 0
for iid, b, fn, ent in work:
    key = s3_service.upload_invoice_pdf(b, filename=fn, entity_id=ent)
    if not key:
        print(f"  upload returned no key inv[{iid}]"); continue
    h = hashlib.sha256(b).hexdigest()
    cur.execute("""UPDATE finance_invoices SET pdf_s3_key=%s, pdf_content_hash=%s,
                   ai_extraction_raw = jsonb_set(ai_extraction_raw,'{recovery,saved_to_s3}','true'::jsonb,true)
                   WHERE id=%s AND pdf_s3_key IS NULL""", (key, h, iid))
    done += cur.rowcount
conn.commit()
cur.execute("SELECT count(*) je FROM finance_journal_entries"); je1 = cur.fetchone()["je"]
cur.execute("SELECT count(*) jl FROM finance_journal_lines"); jl1 = cur.fetchone()["jl"]
print(f"UPLOADED + set pdf_s3_key on {done} invoices | JE {je0}->{je1} JL {jl0}->{jl1}")
assert je1 == je0 and jl1 == jl0, "JE/JL CHANGED"
print("OK — invariant held.")
conn.close()
