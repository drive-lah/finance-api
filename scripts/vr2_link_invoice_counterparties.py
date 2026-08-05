#!/usr/bin/env python
"""
Link NO_COUNTERPARTY invoices to an EXISTING counterparty by their vendor signal
(invoice extraction vendor_name, else Retool payee) — STRICT match only.

Supervised prod write (POL-83): dry-run print -> backup -> guarded UPDATE -> verify.
Only stamps counterparty_id (draft invoices → no JE; JE/JL must be UNCHANGED).
Reversible: backup carries (id, prior counterparty_id=NULL) for every touched row.

Match rule (correctness over recall — Gaurav: "the correct counterparty"):
  normalize = lowercase, alnum-only.
  ACCEPT iff exactly ONE counterparty matches by:
     - normalized-equal, OR
     - substring where the SHORTER normalized string length >= 6.
  Entity-scope: prefer counterparties of the invoice's entity; if the name matches
  in >1 counterparty (after entity scoping) → SKIP (never guess).
"""
import os, re, csv, sys
from datetime import datetime, timezone
import psycopg2
from psycopg2.extras import RealDictCursor

WRITE = "--write" in sys.argv   # default DRY-RUN; pass --write to persist

DB=None
for l in open(os.path.join(os.path.dirname(__file__),"..",".env")):
    if l.startswith("DATABASE_URL="): DB=l.strip().split("=",1)[1].strip().strip('"'); break
conn=psycopg2.connect(DB); c=conn.cursor(cursor_factory=RealDictCursor)

def norm(s): return re.sub(r'[^a-z0-9]','',(s or '').lower())

# existing counterparties (id, name, entity)
c.execute("SELECT id, name, entity_id FROM finance_counterparties WHERE name IS NOT NULL")
CPS=[dict(r) for r in c.fetchall()]
for x in CPS: x["n"]=norm(x["name"])

def match(sig, entity_id):
    n=norm(sig)
    if len(n) < 3: return None, "signal_too_short"
    cands=[]
    for x in CPS:
        k=x["n"]
        if not k: continue
        if k==n or (min(len(k),len(n))>=6 and (k in n or n in k)):
            cands.append(x)
    if not cands: return None, "no_existing_cp"
    # entity scoping
    same=[x for x in cands if x["entity_id"]==entity_id]
    pool=same if same else cands
    uniq={x["id"]:x for x in pool}
    if len(uniq)!=1: return None, f"ambiguous({len(uniq)})"
    return list(uniq.values())[0], "ok"

BASE="""FROM finance_invoices
WHERE (ai_extraction_raw->'recon'->'duplicate'->>'is_duplicate') IS DISTINCT FROM 'true'
  AND (ai_extraction_raw->'provisional_paid'->>'is_provisional_paid')='true'
  AND counterparty_id IS NULL"""
c.execute(f"""SELECT id, entity_id,
   nullif(trim(ai_extraction_raw->'extraction'->>'vendor_name'),'') vname,
   nullif(trim(ai_extraction_raw->'retool_ref'->>'payee'),'') payee
   {BASE}""")
invs=c.fetchall()

links=[]; skips={}
for r in invs:
    sig = r["vname"] or r["payee"]
    if not sig:
        skips["no_vendor_signal"]=skips.get("no_vendor_signal",0)+1; continue
    cp, why = match(sig, r["entity_id"])
    if cp:
        links.append({"invoice_id":r["id"],"counterparty_id":cp["id"],
                      "counterparty_name":cp["name"],"signal":sig})
    else:
        skips[why]=skips.get(why,0)+1

print(f"NO_COUNTERPARTY invoices: {len(invs)}")
print(f"CONFIRMED links (strict, unique): {len(links)}")
print("skips:", skips)
print("\nsample confirmed links:")
for x in links[:25]:
    print(f"  inv[{x['invoice_id']}] {x['signal']!r} -> cp[{x['counterparty_id']}] {x['counterparty_name']!r}")

if not WRITE:
    print("\n[DRY-RUN] no DB write. Re-run with --write to persist.")
    sys.exit(0)

# ---- SUPERVISED WRITE ----
ts=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
bpath=os.path.join(os.path.dirname(__file__),"..","backups",f"invoice_cp_link_backup_{ts}.csv")
with open(bpath,"w",newline="") as f:
    w=csv.writer(f); w.writerow(["invoice_id","prior_counterparty_id","new_counterparty_id","signal"])
    for x in links: w.writerow([x["invoice_id"],"", x["counterparty_id"], x["signal"]])
print(f"\nbackup -> {bpath}")

c.execute("SELECT count(*) je FROM finance_journal_entries"); je0=c.fetchone()["je"]
c.execute("SELECT count(*) jl FROM finance_journal_lines"); jl0=c.fetchone()["jl"]

updated=0
for x in links:
    c.execute("""UPDATE finance_invoices SET counterparty_id=%s
                 WHERE id=%s AND counterparty_id IS NULL""",(x["counterparty_id"],x["invoice_id"]))
    updated+=c.rowcount
conn.commit()

c.execute("SELECT count(*) je FROM finance_journal_entries"); je1=c.fetchone()["je"]
c.execute("SELECT count(*) jl FROM finance_journal_lines"); jl1=c.fetchone()["jl"]
c.execute(f"SELECT count(*) n {BASE}"); still_null=c.fetchone()["n"]

print(f"UPDATED counterparty_id on {updated} invoices")
print(f"JE {je0}->{je1}  JL {jl0}->{jl1}  (must be unchanged)")
print(f"NO_COUNTERPARTY remaining: {still_null}")
assert je1==je0 and jl1==jl0, "JE/JL CHANGED — investigate!"
assert updated==len(links), "update count mismatch"
print("OK — invariant held.")
conn.close()
