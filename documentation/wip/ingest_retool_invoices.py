#!/usr/bin/env python3
"""
Ingest Retool finance_db third-party (vendor) invoices into finance_invoices (DRAFT).

Per-row pipeline:
  1. no attachment           -> manifest outcome 'no_attachment' (can't truth-source)
  2. download PDF/image, SHA-256
  3. content-hash dedup       -> outcome 'duplicate' (dup_reason=hash, duplicate_of recorded)
  4. extract (existing ai_extraction_service) — invoice IS the source of truth
  5. non-invoice gate: is_invoice=false / doc_type in {statement,letter,report,screenshot}
                             -> outcome 'not_invoice' (NOT booked)
  6. counterparty match (fuzzy, MATCH-ONLY; unmatched -> counterparty NULL = quarantine)
  7. entity resolution (POL-27): bill_to -> platform -> counterparty home entity -> currency
                             + currency_entity_flag for review (never a veto)
  8. copy PDF to OUR S3, insert DRAFT finance_invoices row (native cols + ai_extraction_raw blob),
     sync_run_id stamped. Semantic-key collision (migration 017) -> outcome 'duplicate' (semantic).

Everything lands in DRAFT — nothing books. One finance_sync_runs receipt + ONE manifest CSV
(one row per Retool finance_db_id, kept updated as it runs) is the single source of what happened.

Usage: python3 ingest_retool_invoices.py /tmp/vendor_all.json
"""
import os, sys, csv, json, hashlib, urllib.request
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
from src.models.sync_run import FinanceSyncRun  # noqa: F401 — registers FK target in ORM metadata

SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/vendor_all.json"
MANIFEST = os.path.join(os.path.dirname(__file__), "VENDOR_INGEST_MANIFEST.csv")
engine = create_engine(os.getenv("DATABASE_URL"))

MANIFEST_COLS = ["finance_db_id","payee","amount","currency","retool_status","platform",
    "outcome","invoice_id","duplicate_of","dup_reason","is_invoice","document_type",
    "entity_id","entity_source","currency_entity_flag","counterparty_id","vendor_flag",
    "coa","run_id","timestamp"]

def ext_of(u):
    u=(u or "").lower()
    for x in (".pdf",".png",".jpeg",".jpg"):
        if u.endswith(x): return x
    return ".pdf"

def dpart(s):
    return str(s)[:10] if s else None

def resolve_entity(billto, platform, cp, currency):
    """POL-27: invoice belongs to the entity it is BILLED TO. Never assign by payer."""
    def name_to_entity(s):
        s=(s or "").lower()
        if "ventures" in s or "holding" in s: return 1
        if "australia" in s or "pty" in s: return 3
        if "singapore" in s or "pte" in s: return 2
        return None
    e = name_to_entity(billto)
    if e: return e, "bill_to"
    p=(platform or "").lower()
    if "lah" in p: return 2, "platform"
    if "mate" in p: return 3, "platform"
    if cp is not None and getattr(cp, "entity_id", None): return cp.entity_id, "counterparty"
    c=(currency or "").upper()
    if c=="SGD": return 2, "currency"
    if c=="AUD": return 3, "currency"
    return 1, "default"   # USD / no signal -> Ventures (review)

def currency_entity_flag(entity_id, currency):
    """Hard contradictions only: SGD not on SG, AUD not on AU. USD is vendor-dependent (no flag)."""
    c=(currency or "").upper()
    if c=="SGD" and entity_id!=2: return True
    if c=="AUD" and entity_id!=3: return True
    return False

def main():
    rows=json.load(open(SRC))
    write_header = not os.path.exists(MANIFEST)
    mf=open(MANIFEST, "a", newline="")
    mw=csv.DictWriter(mf, fieldnames=MANIFEST_COLS)
    if write_header: mw.writeheader()

    with Session(engine) as db:
        vendors=db.query(FinanceCounterparty).filter(
            FinanceCounterparty.type==CounterpartyType.VENDOR.value,
            FinanceCounterparty.status=="active").all()
        entity_names=[r[0] for r in db.execute(text("select name from finance_entities")).all()]
        run_id=db.execute(text("insert into finance_sync_runs (source,status,started_at) "
            "values ('retool_invoice','RUNNING',now()) returning id")).scalar()
        db.commit()
        print(f"sync_run_id={run_id} | {len(rows)} rows | manifest={MANIFEST}", flush=True)

        seen={}
        tally={k:0 for k in ("uploaded","duplicate","not_invoice","no_attachment","error")}
        for i,r in enumerate(rows,1):
            fid=r.get("id")
            rec=dict.fromkeys(MANIFEST_COLS, "")
            rec.update(finance_db_id=fid, payee=r.get("third_party_payee"), amount=r.get("amount"),
                currency=r.get("currency"), retool_status=r.get("status"), platform=r.get("platform"),
                run_id=run_id, timestamp=datetime.utcnow().isoformat())
            try:
                url=r.get("attachment_1")
                if not url:
                    rec["outcome"]="no_attachment"; tally["no_attachment"]+=1; mw.writerow(rec); mf.flush()
                    print(f"[{i:4}] {fid} NO_ATTACH", flush=True); continue
                blob=urllib.request.urlopen(url, timeout=45).read()
                h=hashlib.sha256(blob).hexdigest()
                dup_of=seen.get(h)
                if not dup_of:
                    ex_row=db.query(FinanceInvoice).filter(FinanceInvoice.pdf_content_hash==h).first()
                    if ex_row: dup_of=f"inv#{ex_row.id}"
                if dup_of:
                    rec.update(outcome="duplicate", dup_reason="hash", duplicate_of=dup_of)
                    tally["duplicate"]+=1; mw.writerow(rec); mf.flush(); print(f"[{i:4}] {fid} DUP(hash)->{dup_of}", flush=True); continue

                ex=ai_extraction_service.extract_invoice_data(blob, entity_names=entity_names, file_extension=ext_of(url))
                ex_err=ex.get("extraction_error")
                is_inv=ex.get("is_invoice"); doc_type=ex.get("document_type")
                rec.update(is_invoice=is_inv, document_type=doc_type)
                # Option B: non-invoices are NOT skipped — they become a flagged DRAFT (visible, never booked).
                doc_gate="not_invoice" if (is_inv is False or doc_type in ("statement","letter","report","spreadsheet_screenshot")) else "ok"

                ev=ex.get("vendor_name") or r.get("third_party_payee") or ""
                cp,conf=fuzzy_match_vendor(ev, vendors)
                cp_id=cp.id if cp else None
                contra=(cp.default_account_code if cp else None) or None
                entity_id,ent_src=resolve_entity(ex.get("bill_to_entity_hint"), r.get("platform"), cp, ex.get("currency") or r.get("currency"))
                cflag=currency_entity_flag(entity_id, ex.get("currency") or r.get("currency"))
                total=ex.get("total_amount") if ex.get("total_amount") is not None else r.get("amount")
                currency=(ex.get("currency") or r.get("currency") or "SGD")[:3]
                inv_date=dpart(ex.get("invoice_date")) or dpart(r.get("service_start_date")) or dpart(r.get("created_at"))
                if total is None or inv_date is None:
                    rec.update(outcome="not_invoice", document_type=doc_type or "other")
                    tally["not_invoice"]+=1; mw.writerow(rec); mf.flush(); print(f"[{i:4}] {fid} NOT_INVOICE(no amt/date)", flush=True); continue

                s3_key=s3_service.upload_invoice_pdf(blob, filename=os.path.basename(url), entity_id=entity_id)
                recon={
                    "extraction":{k:ex.get(k) for k in ("vendor_name","vendor_tax_id","invoice_number",
                        "invoice_date","due_date","total_amount","subtotal_amount","tax_amount","currency",
                        "service_period_start","service_period_end","description","suggested_coa_account",
                        "bill_to_entity_hint","is_invoice","document_type","confidence")},
                    "retool_ref":{"finance_db_id":fid,"payee":r.get("third_party_payee"),"amount":r.get("amount"),
                        "currency":r.get("currency"),"sub_category":r.get("sub_category"),"category":r.get("category"),
                        "status":r.get("status"),"type":r.get("type"),"platform":r.get("platform"),
                        "trip_id":r.get("trip_id"),"created_at":r.get("created_at"),"closed_at":r.get("closed_at")},
                    "provisional_paid":{"is_provisional_paid":r.get("status")=="Closed","provisional_paid_at":r.get("closed_at")},
                    "recon":{"amount_match":(ex.get("total_amount") is not None and abs(float(ex.get("total_amount"))-float(r.get("amount") or 0))<=0.01),
                        "vendor_confidence":round(conf,2),"vendor_flag":"MATCHED" if cp else "QUARANTINE",
                        "coa_flag":"OK" if contra else ("NEEDS-COA" if cp else "NO-COUNTERPARTY"),
                        "entity_source":ent_src,"currency_entity_flag":cflag,"document_gate":doc_gate,"extraction_error":ex_err}}
                inv=FinanceInvoice(entity_id=entity_id, counterparty_id=cp_id,
                    invoice_number=(ex.get("invoice_number") or None), invoice_date=inv_date,
                    due_date=dpart(ex.get("due_date")) or dpart(r.get("due_date")), total_amount=total,
                    net_amount=ex.get("subtotal_amount"), tax_amount=ex.get("tax_amount"), currency=currency,
                    contra_account_code=contra, status="draft",
                    service_period_start=dpart(ex.get("service_period_start")),
                    service_period_end=dpart(ex.get("service_period_end")), has_amortization_schedule=False,
                    ai_extraction_raw=recon, ai_confidence_score=ex.get("confidence"),
                    contract_matched=False, new_vendor=False, coa_source=("db" if contra else None),
                    uploaded_by="ingest:retool_invoice", pdf_s3_key=s3_key, pdf_content_hash=h, sync_run_id=run_id)
                try:
                    db.add(inv); db.commit()
                except IntegrityError:
                    db.rollback()
                    coll=db.query(FinanceInvoice).filter(FinanceInvoice.entity_id==entity_id,
                        FinanceInvoice.counterparty_id==cp_id, FinanceInvoice.invoice_number==(ex.get("invoice_number") or None),
                        FinanceInvoice.currency==currency).first()
                    rec.update(outcome="duplicate", dup_reason="semantic", duplicate_of=(f"inv#{coll.id}" if coll else "?"),
                        entity_id=entity_id, entity_source=ent_src)
                    tally["duplicate"]+=1; mw.writerow(rec); mf.flush(); print(f"[{i:4}] {fid} DUP(semantic)", flush=True); continue
                seen[h]=f"inv#{inv.id}"
                oc="not_invoice" if doc_gate=="not_invoice" else "uploaded"
                rec.update(outcome=oc, invoice_id=inv.id, entity_id=entity_id, entity_source=ent_src,
                    currency_entity_flag=cflag, counterparty_id=cp_id, vendor_flag=("MATCHED" if cp else "QUARANTINE"), coa=contra)
                tally[oc]+=1; mw.writerow(rec); mf.flush()
                print(f"[{i:4}] {fid} {oc.upper()} inv#{inv.id} ent={entity_id}/{ent_src} vend={'#'+str(cp_id) if cp_id else 'QUAR'} coa={contra or '-'}{' FLAG' if cflag else ''}", flush=True)
            except Exception as e:
                db.rollback(); rec.update(outcome="error", dup_reason=str(e)[:60]); tally["error"]+=1
                mw.writerow(rec); mf.flush(); print(f"[{i:4}] {fid} ERR {str(e)[:70]}", flush=True)

        detail=json.dumps(tally)
        db.execute(text("update finance_sync_runs set status='DONE', finished_at=now(), "
            "fetched=:f, created=:c, duplicates=:d, detail=:det where id=:id"),
            {"f":len(rows),"c":tally["uploaded"],"d":tally["duplicate"],"det":detail,"id":run_id})
        db.commit()
    mf.close()
    print(f"\n=== RUN {run_id} DONE === {tally}", flush=True)

if __name__=="__main__":
    main()
