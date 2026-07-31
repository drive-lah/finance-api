#!/usr/bin/env python3
"""
Ingest a batch of Google-Drive vendor-invoice files into finance_invoices (DRAFT),
DEDUP-FIRST against the already-loaded corpus.

Input: JSON array of {drive_id, name, local_path} (files already downloaded to disk
by the orchestrating agent via the Google Drive MCP).

Per file:
  1. SHA-256 the local file.
  2. hash already in finance_invoices.pdf_content_hash -> 'covered' (identical file
     already loaded from Retool; skip, NO extraction cost).
  3. else extract (existing ai_extraction_service) -> is_invoice gate (flagged draft
     if not an invoice) -> vendor match -> entity (bill_to -> currency -> default; NO
     platform, Drive has none) -> S3 -> DRAFT finance_invoices with a gdrive_ref blob.
  4. DB semantic unique index (migration 017) auto-catches same-invoice-different-file
     -> 'duplicate'. So only genuine Drive-only strays become new DRAFTs.

Appends to documentation/wip/VENDOR_DRIVE_MANIFEST.csv. Nothing books.
Usage: python3 ingest_drive_invoices.py /tmp/drive_batch.json
"""
import os, sys, csv, json, hashlib
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from src.services.ai_extraction_service import ai_extraction_service
from src.services.vendor_matching_service import fuzzy_match_vendor
from src.services.s3_service import s3_service
from src.models.invoice import FinanceInvoice
from src.models.counterparty import FinanceCounterparty, CounterpartyType
from src.models.sync_run import FinanceSyncRun  # noqa: F401

BATCH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/drive_batch.json"
MANIFEST = os.path.join(os.path.dirname(__file__), "VENDOR_DRIVE_MANIFEST.csv")
engine = create_engine(os.getenv("DATABASE_URL"))
COLS = ["drive_id","name","outcome","invoice_id","is_invoice","document_type","entity_id",
        "entity_source","counterparty_id","vendor_flag","coa","duplicate_of","timestamp"]

def dpart(s): return str(s)[:10] if s else None
def ext_of(n):
    n=(n or "").lower()
    for x in (".pdf",".png",".jpeg",".jpg"):
        if n.endswith(x): return x
    return ".pdf"

def resolve_entity(billto, cp, currency):
    def n2e(s):
        s=(s or "").lower()
        if "ventures" in s or "holding" in s: return 1
        if "australia" in s or "pty" in s: return 3
        if "singapore" in s or "pte" in s: return 2
        return None
    e=n2e(billto)
    if e: return e,"bill_to"
    if cp is not None and getattr(cp,"entity_id",None): return cp.entity_id,"counterparty"
    c=(currency or "").upper()
    if c=="SGD": return 2,"currency"
    if c=="AUD": return 3,"currency"
    return 1,"default"

def main():
    files=json.load(open(BATCH))
    hdr=not os.path.exists(MANIFEST)
    mf=open(MANIFEST,"a",newline=""); mw=csv.DictWriter(mf,fieldnames=COLS)
    if hdr: mw.writeheader()
    with Session(engine) as db:
        vendors=db.query(FinanceCounterparty).filter(
            FinanceCounterparty.type==CounterpartyType.VENDOR.value,
            FinanceCounterparty.status=="active").all()
        ent=[r[0] for r in db.execute(text("select name from finance_entities")).all()]
        run_id=db.execute(text("insert into finance_sync_runs (source,status,started_at) values ('gdrive_invoice','RUNNING',now()) returning id")).scalar()
        db.commit()
        from collections import Counter; tally=Counter()
        for i,f in enumerate(files,1):
            rec=dict.fromkeys(COLS,""); rec.update(drive_id=f["drive_id"],name=f["name"],timestamp=datetime.utcnow().isoformat())
            try:
                blob=open(f["local_path"],"rb").read(); h=hashlib.sha256(blob).hexdigest()
                hit=db.query(FinanceInvoice).filter(FinanceInvoice.pdf_content_hash==h).first()
                if hit:
                    rec.update(outcome="covered",duplicate_of=f"inv#{hit.id}"); tally["covered"]+=1
                    mw.writerow(rec); mf.flush(); print(f"[{i:3}] {f['name'][:40]} COVERED inv#{hit.id}",flush=True); continue
                ex=ai_extraction_service.extract_invoice_data(blob,entity_names=ent,file_extension=ext_of(f["name"]))
                is_inv=ex.get("is_invoice"); dt=ex.get("document_type")
                gate="not_invoice" if (is_inv is False or dt in ("statement","letter","report","spreadsheet_screenshot")) else "ok"
                ev=ex.get("vendor_name") or ""
                cp,conf=fuzzy_match_vendor(ev,vendors); cp_id=cp.id if cp else None
                contra=(cp.default_account_code if cp else None) or None
                eid,esrc=resolve_entity(ex.get("bill_to_entity_hint"),cp,ex.get("currency"))
                total=ex.get("total_amount"); idate=dpart(ex.get("invoice_date"))
                if total is None or idate is None:
                    rec.update(outcome="not_invoice",is_invoice=is_inv,document_type=dt or "other"); tally["not_invoice"]+=1
                    mw.writerow(rec); mf.flush(); print(f"[{i:3}] {f['name'][:40]} NOT_INVOICE(no amt/date)",flush=True); continue
                s3k=s3_service.upload_invoice_pdf(blob,filename=f["name"],entity_id=eid)
                recon={"extraction":{k:ex.get(k) for k in ("vendor_name","vendor_tax_id","invoice_number","invoice_date","due_date","total_amount","subtotal_amount","tax_amount","currency","service_period_start","service_period_end","description","suggested_coa_account","bill_to_entity_hint","is_invoice","document_type","confidence")},
                       "gdrive_ref":{"drive_id":f["drive_id"],"name":f["name"]},
                       "provisional_paid":{"is_provisional_paid":False,"provisional_paid_at":None},
                       "recon":{"amount_match":None,"vendor_confidence":round(conf,2),"vendor_flag":"MATCHED" if cp else "QUARANTINE",
                                "coa_flag":"OK" if contra else ("NEEDS-COA" if cp else "NO-COUNTERPARTY"),"entity_source":esrc,
                                "currency_entity_flag":False,"document_gate":gate,"extraction_error":ex.get("extraction_error")}}
                inv=FinanceInvoice(entity_id=eid,counterparty_id=cp_id,invoice_number=(ex.get("invoice_number") or None),
                    invoice_date=idate,due_date=dpart(ex.get("due_date")),total_amount=total,net_amount=ex.get("subtotal_amount"),
                    tax_amount=ex.get("tax_amount"),currency=(ex.get("currency") or "SGD")[:3],contra_account_code=contra,status="draft",
                    service_period_start=dpart(ex.get("service_period_start")),service_period_end=dpart(ex.get("service_period_end")),
                    has_amortization_schedule=False,ai_extraction_raw=recon,ai_confidence_score=ex.get("confidence"),
                    contract_matched=False,new_vendor=False,coa_source=("db" if contra else None),
                    uploaded_by="ingest:gdrive",pdf_s3_key=s3k,pdf_content_hash=h,sync_run_id=run_id)
                try:
                    db.add(inv); db.commit()
                except IntegrityError:
                    db.rollback()
                    coll=db.query(FinanceInvoice).filter(FinanceInvoice.counterparty_id==cp_id,FinanceInvoice.invoice_number==(ex.get("invoice_number") or None),FinanceInvoice.entity_id==eid).first()
                    rec.update(outcome="duplicate",duplicate_of=(f"inv#{coll.id}" if coll else "?")); tally["duplicate"]+=1
                    mw.writerow(rec); mf.flush(); print(f"[{i:3}] {f['name'][:40]} DUPLICATE",flush=True); continue
                oc="not_invoice" if gate=="not_invoice" else "stray_loaded"
                rec.update(outcome=oc,invoice_id=inv.id,is_invoice=is_inv,document_type=dt,entity_id=eid,entity_source=esrc,
                           counterparty_id=cp_id,vendor_flag=("MATCHED" if cp else "QUARANTINE"),coa=contra); tally[oc]+=1
                mw.writerow(rec); mf.flush(); print(f"[{i:3}] {f['name'][:40]} {oc.upper()} inv#{inv.id} ent={eid}/{esrc}",flush=True)
            except Exception as e:
                db.rollback(); rec.update(outcome="error",duplicate_of=str(e)[:50]); tally["error"]+=1
                mw.writerow(rec); mf.flush(); print(f"[{i:3}] {f.get('name','?')[:40]} ERR {str(e)[:60]}",flush=True)
        db.execute(text("update finance_sync_runs set status='DONE',finished_at=now(),fetched=:f,created=:c,detail=:d where id=:id"),
                   {"f":len(files),"c":tally.get("stray_loaded",0),"d":json.dumps(dict(tally)),"id":run_id})
        db.commit()
    mf.close(); print(f"\n=== DRIVE BATCH DONE === {dict(tally)}",flush=True)

if __name__=="__main__": main()
