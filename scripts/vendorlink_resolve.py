#!/usr/bin/env python
"""
Apply Gaurav's VENDORLINK remarks (documentation/wip/VENDORLINK_PROPOSALS.csv 'My remarks').
Buckets:
  LINK            -> stamp an existing counterparty_id
  CREATE_VENDOR   -> create a new vendor counterparty, then stamp
  CREATE_EMPLOYEE -> create a new employee counterparty, then stamp
  EXCLUDE         -> not a vendor / internal / do-not-create -> flag recon.vendorlink_disposition, no cp
  MERGE 693->752  -> re-point all invoices+txns from 693 to 752, retire 693
  DEFAULT accept  -> un-remarked EXACT/SUBSTR: stamp the proposed existing cp
  HOLD            -> un-remarked NEW: leave, flag needs_review (never auto-create)
Draft invoices -> no JE. Supervised prod write (POL-83): backup + JE/JL invariant. Default DRY-RUN; --write to apply.
"""
import os, sys, csv, json
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
for l in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
    l = l.strip()
    if l and not l.startswith("#") and "=" in l:
        k, v = l.split("=", 1); os.environ.setdefault(k, v.strip().strip('"'))
WRITE = "--write" in sys.argv
from src.database import get_session_factory
from src.models.invoice import FinanceInvoice
from src.models.counterparty import FinanceCounterparty
from sqlalchemy import text
from sqlalchemy.orm.attributes import flag_modified

LINK = {381:163, 437:163, 803:655, 2443:28,
        1898:671, 2311:671, 2325:671,
        2402:752, 2403:752, 2437:752, 2406:752, 2416:752}
CREATE_VENDOR = {447:"Northside Autocare Pty Ltd", 530:"SCI (Singapore Continuation Institute)",
                 762:"Car Key Depot Pte Ltd", 816:"CAIDA", 2373:"Spares Australia Melbourne Pty Ltd"}
CREATE_EMPLOYEE = {2464:"Mart"}
EXCLUDE = {260:"not_a_vendor",364:"not_a_vendor",406:"not_a_vendor",535:"not_a_vendor",641:"not_a_vendor",
           769:"not_a_vendor",967:"not_a_vendor",1447:"internal_own",2372:"internal_own",2010:"internal_own",
           452:"do_not_create",458:"do_not_create",1564:"do_not_create",1579:"do_not_create",1920:"do_not_create",
           1976:"do_not_create",2259:"do_not_create",2457:"do_not_create"}
MERGE_FROM, MERGE_TO = 693, 752

Session = get_session_factory(); db = Session()
je0 = db.execute(text("SELECT count(*) FROM finance_journal_entries")).scalar()
jl0 = db.execute(text("SELECT count(*) FROM finance_journal_lines")).scalar()

rows = list(csv.DictReader(open(os.path.join(os.path.dirname(__file__),"..","documentation/wip/VENDORLINK_PROPOSALS.csv"), encoding="latin-1")))
stats = {"link":0,"create_vendor":0,"create_employee":0,"exclude":0,"accept":0,"hold":0}
backup = {}

def stamp(inv, cp_id):
    inv.counterparty_id = cp_id
    cp = db.get(FinanceCounterparty, cp_id)
    if cp and not inv.contra_account_code and cp.default_account_code:
        inv.contra_account_code = cp.default_account_code; inv.coa_source = "db"

def new_cp(name, typ, entity_id):
    cp = FinanceCounterparty(name=name, type=typ, entity_id=entity_id)
    db.add(cp); db.flush()   # get id
    return cp.id

for r in rows:
    iid = int(r["invoice_id"]); mt = r["match_type"]
    inv = db.get(FinanceInvoice, iid)
    if not inv: continue
    backup[str(iid)] = {"counterparty_id": inv.counterparty_id, "contra_account_code": inv.contra_account_code}
    if iid in EXCLUDE:
        raw = dict(inv.ai_extraction_raw or {}); rec = dict(raw.get("recon") or {})
        rec["vendorlink_disposition"] = EXCLUDE[iid]; raw["recon"] = rec
        inv.ai_extraction_raw = raw; flag_modified(inv, "ai_extraction_raw"); stats["exclude"]+=1
    elif iid in LINK:
        stamp(inv, LINK[iid]); stats["link"]+=1
    elif iid in CREATE_VENDOR:
        cid = new_cp(CREATE_VENDOR[iid], "vendor", inv.entity_id); stamp(inv, cid); stats["create_vendor"]+=1
    elif iid in CREATE_EMPLOYEE:
        cid = new_cp(CREATE_EMPLOYEE[iid], "employee", inv.entity_id); stamp(inv, cid); stats["create_employee"]+=1
    elif mt in ("EXACT","SUBSTR") and r["proposed_counterparty_id"].strip():
        stamp(inv, int(r["proposed_counterparty_id"])); stats["accept"]+=1
    elif mt == "NEW":
        raw = dict(inv.ai_extraction_raw or {}); rec = dict(raw.get("recon") or {})
        rec["vendorlink_disposition"] = "hold_review"; raw["recon"] = rec
        inv.ai_extraction_raw = raw; flag_modified(inv, "ai_extraction_raw"); stats["hold"]+=1
    # NONE -> no action

# MERGE 693 -> 752: re-point all references, retire 693
merge_inv = db.execute(text("SELECT count(*) FROM finance_invoices WHERE counterparty_id=:f"), {"f":MERGE_FROM}).scalar()
merge_txn = db.execute(text("SELECT count(*) FROM finance_transactions WHERE counterparty_id=:f"), {"f":MERGE_FROM}).scalar()
print(f"MERGE {MERGE_FROM}->{MERGE_TO}: {merge_inv} invoices + {merge_txn} txns to re-point")
print("planned:", stats)

if not WRITE:
    db.rollback(); print("[DRY-RUN] rolled back."); sys.exit(0)

ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
json.dump(backup, open(os.path.join(os.path.dirname(__file__),"..","backups",f"vendorlink_resolve_backup_{ts}.json"),"w"))
db.execute(text("UPDATE finance_invoices SET counterparty_id=:t WHERE counterparty_id=:f"), {"t":MERGE_TO,"f":MERGE_FROM})
db.execute(text("UPDATE finance_transactions SET counterparty_id=:t WHERE counterparty_id=:f"), {"t":MERGE_TO,"f":MERGE_FROM})
db.execute(text("UPDATE finance_counterparties SET name = name || ' [MERGED->752]' WHERE id=:f"), {"f":MERGE_FROM})
db.commit()
je1 = db.execute(text("SELECT count(*) FROM finance_journal_entries")).scalar()
jl1 = db.execute(text("SELECT count(*) FROM finance_journal_lines")).scalar()
print(f"COMMITTED | JE {je0}->{je1} JL {jl0}->{jl1}")
assert je1==je0 and jl1==jl0, "JE/JL CHANGED"
print("OK — invariant held.")
db.close()
