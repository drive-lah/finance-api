#!/usr/bin/env python
"""
Resolve recovered-invoice conflicts by letting RETOOL WIN (Gaurav 2026-08-03).
For every recovered invoice, apply the Retool finance_db values as authoritative:
  - invoice_date   <- Retool created_date
  - total_amount   <- Retool amount (+ currency), when Retool amount > 0
  - counterparty   <- match_or_create(Retool third_party_payee), when payee present
Also stamps the counterparty default COA when the invoice has none, and records
recovery.resolution = 'retool_win'. Reads Retool values from finance_retool_meta.

Draft invoices only -> no JE. Supervised prod write (POL-83): backup + JE/JL invariant.
Default DRY-RUN; add --write.
"""
import os, sys, json
from datetime import datetime, timezone, date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
for l in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
    l = l.strip()
    if l and not l.startswith("#") and "=" in l:
        k, v = l.split("=", 1); os.environ.setdefault(k, v.strip().strip('"'))

WRITE = "--write" in sys.argv
from src.database import get_session_factory
from src.models.invoice import FinanceInvoice
from src.models.counterparty import FinanceCounterparty
from src.services.vendor_matching_service import vendor_matching_service
from sqlalchemy import text
from sqlalchemy.orm.attributes import flag_modified

Session = get_session_factory()
db = Session()

je0 = db.execute(text("SELECT count(*) FROM finance_journal_entries")).scalar()
jl0 = db.execute(text("SELECT count(*) FROM finance_journal_lines")).scalar()

meta = {r[0]: {"amount": r[1], "currency": r[2], "payee": r[3], "created": r[4]}
        for r in db.execute(text("SELECT finance_db_id,amount,currency,payee,created_date FROM finance_retool_meta")).all()}
print(f"retool-meta rows: {len(meta)}")

# recovered invoices, joined to their finance_db_id
invs = db.execute(text("""
  SELECT id, (ai_extraction_raw->'retool_ref'->>'finance_db_id')::int fid
  FROM finance_invoices WHERE ai_extraction_raw ? 'recovery'
""")).all()

stats = {"date_set": 0, "amount_set": 0, "cp_set": 0, "cp_created": 0, "coa_set": 0, "no_meta": 0}
backup = {}
for iid, fid in invs:
    m = meta.get(fid)
    if not m:
        stats["no_meta"] += 1; continue
    inv = db.get(FinanceInvoice, iid)
    backup[str(iid)] = {"invoice_date": str(inv.invoice_date), "total_amount": str(inv.total_amount),
                        "currency": inv.currency, "counterparty_id": inv.counterparty_id,
                        "contra_account_code": inv.contra_account_code}
    # 1) invoice_date <- retool created
    if m["created"]:
        inv.invoice_date = m["created"]; stats["date_set"] += 1
    # 2) amount <- retool (guard > 0)
    if m["amount"] is not None and float(m["amount"]) > 0:
        inv.total_amount = m["amount"]
        if m["currency"]:
            inv.currency = m["currency"][:3]
        stats["amount_set"] += 1
    # 3) counterparty <- retool payee (match or create)
    payee = (m["payee"] or "").strip()
    if payee:
        cp, is_new, _conf = vendor_matching_service.match_or_create(db, payee, None)
        if cp:
            inv.counterparty_id = cp.id; stats["cp_set"] += 1
            if is_new: stats["cp_created"] += 1
            if not inv.contra_account_code and cp.default_account_code:
                inv.contra_account_code = cp.default_account_code; inv.coa_source = "db"; stats["coa_set"] += 1
    # mark resolution in the recovery block
    raw = dict(inv.ai_extraction_raw or {})
    recn = dict(raw.get("recovery") or {})
    recn["resolution"] = "retool_win"
    recn["resolved_at"] = datetime.now(timezone.utc).isoformat()
    raw["recovery"] = recn
    inv.ai_extraction_raw = raw
    flag_modified(inv, "ai_extraction_raw")

print("planned changes:", stats)
if not WRITE:
    db.rollback()
    print("[DRY-RUN] rolled back, nothing written. --write to apply."); sys.exit(0)

ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
bpath = os.path.join(os.path.dirname(__file__), "..", "backups", f"retool_win_resolution_backup_{ts}.json")
json.dump(backup, open(bpath, "w"))
print(f"backup -> {bpath}")
db.commit()
je1 = db.execute(text("SELECT count(*) FROM finance_journal_entries")).scalar()
jl1 = db.execute(text("SELECT count(*) FROM finance_journal_lines")).scalar()
print(f"COMMITTED | JE {je0}->{je1} JL {jl0}->{jl1}")
assert je1 == je0 and jl1 == jl0, "JE/JL CHANGED"
print("OK — invariant held.")
db.close()
