"""
Invoice Service

Business logic for managing invoices in the Accounts Payable workflow.
Handles creation, approval (with JE generation), rejection, voiding,
payment recording, AP knock-off lookups, and the AI contract review gate.
"""
import json
import logging
import os
from datetime import datetime, date, UTC
from typing import TYPE_CHECKING, Optional, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.invoice import FinanceInvoice, InvoiceStatus
from src.models.contract import FinanceAmortizationSchedule, FinanceContract, FinanceApprovalRule
from src.models.counterparty import FinanceCounterparty
from src.models.schemas import InvoiceCreate, InvoiceUpdate
from src.services.journal_service import journal_service
from src.utils.errors import NotFoundError

if TYPE_CHECKING:
    from src.models.bank_account import FinanceBankAccount
    from src.models.journal_entry import FinanceJournalEntry
    from src.models.transaction import FinanceTransaction
    from anthropic.types import TextBlock

logger = logging.getLogger(__name__)

# Standard AP liability account
AP_ACCOUNT_CODE = "2000"
# Prepaid asset account for amortization (COA: 1300 Prepayments; 1200 is Trade Receivables)
PREPAID_ACCOUNT_CODE = "1300"
# GST / VAT input tax credit (recoverable on purchases)
GST_INPUT_ACCOUNT_CODE = "1350"

# ── Intercompany AP account codes ─────────────────────────────────────────────
# Keyed by (bank_entity_short_name, invoice_entity_short_name).
# Short name = last word of FinanceEntity.name (e.g., "DL SG" → "SG").
# Repointed 2026-08-03 to the ACTIVE 8200 NET series (POL-93/94). The old split
# 8000/8100 accounts are SUSPENDED (would fail the active-account guard). Net model:
# ONE account per (books-entity, counterparty-entity) — 8200 IC-Australia(SG books),
# 8201 IC-Ventures(SG), 8210 IC-Singapore(AU), 8211 IC-Ventures(AU),
# 8220 IC-Singapore(Ventures), 8221 IC-Australia(Ventures).
# Receivable = the BANK entity's net account re the invoice entity  = NET[(bank, invoice)].
# Payable    = the INVOICE entity's net account re the bank entity   = NET[(invoice, bank)].
_IC_RECEIVABLE_CODES: dict[tuple[str, str], str] = {
    ("SG", "AU"):       "8200",  # SG books, re AU
    ("SG", "Ventures"): "8201",  # SG books, re Ventures
    ("AU", "SG"):       "8210",  # AU books, re SG
    ("AU", "Ventures"): "8211",  # AU books, re Ventures
    ("Ventures", "SG"): "8220",  # Ventures books, re SG
    ("Ventures", "AU"): "8221",  # Ventures books, re AU
}
# NOTE: _get_ic_codes looks this up with the FLIPPED key (invoice_short, bank_short),
# so it holds the SAME net-account values as the receivable map (each = the FIRST
# entity's net account re the SECOND). Keyed (books-entity, counterparty-entity).
_IC_PAYABLE_CODES: dict[tuple[str, str], str] = {
    ("SG", "AU"):       "8200",  # SG books, re AU
    ("SG", "Ventures"): "8201",  # SG books, re Ventures
    ("AU", "SG"):       "8210",  # AU books, re SG
    ("AU", "Ventures"): "8211",  # AU books, re Ventures
    ("Ventures", "SG"): "8220",  # Ventures books, re SG
    ("Ventures", "AU"): "8221",  # Ventures books, re AU
}


def _coerce_amount(raw: object) -> float | None:
    """Parse an extractor amount to float, locale-aware. Returns None (never a
    silently-wrong value) when it cannot parse — the caller surfaces that.

    Handles: native numbers; US `1,234.56`; EU `1.234,56`; parenthesised
    negatives `(500.00)`; currency symbols/spaces. Ambiguity rule: when both
    separators appear, the LAST one is the decimal point; a lone comma with a
    2-digit tail is decimal, otherwise a thousands separator.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    import re as _re
    s = str(raw).strip()
    neg = s.startswith("(") and s.endswith(")")
    s = _re.sub(r"[^0-9.,-]", "", s)  # drop currency symbols, spaces, letters
    if not s or s in {"-", ".", ","}:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")   # EU: 1.234,56 -> 1234.56
        else:
            s = s.replace(",", "")                       # US: 1,234.56 -> 1234.56
    elif "," in s:
        s = s.replace(",", ".") if len(s.rsplit(",", 1)[1]) == 2 else s.replace(",", "")
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def _entity_short(name: str) -> str:
    """Resolve an entity name to its IC-map key: SG / AU / Ventures.

    Handles both seed-style names ('DL SG') and the live DB's full legal names
    ('Drive lah Ventures Holding Pte Ltd', 'Drive lah Australia Pty Ltd') —
    a plain last-word split returns 'Ltd' for every live entity, so no IC
    pair would ever match.
    """
    n = name.strip().lower()
    if "ventures" in n:
        return "Ventures"
    if "australia" in n or n.endswith(" au"):
        return "AU"
    return "SG"


def _invoice_dict(invoice: "FinanceInvoice", db: Optional[Session] = None) -> dict:
    from src.models.schemas import InvoiceResponse
    from src.services.s3_service import s3_service

    result = InvoiceResponse.model_validate(invoice).model_dump()

    # Add pre-signed URL if S3 file exists
    if invoice.pdf_s3_key:
        presigned_url = s3_service.get_presigned_url(invoice.pdf_s3_key, expiration_seconds=3600)
        result["invoice_url"] = presigned_url

    # Add counterparty data if available (for COA defaults in approval modal)
    if db and invoice.counterparty_id:
        counterparty = db.get(FinanceCounterparty, invoice.counterparty_id)
        if counterparty:
            result["counterparty"] = {
                "id": counterparty.id,
                "name": counterparty.name,
                "default_account_code": counterparty.default_account_code,
            }

    # Transitional "Retool tags" for the review UI — derived from ai_extraction_raw,
    # NO DB columns (Retool is a temporary migration source). Render as badges
    # (Retool #id · DUP→#orig · Paid/Unpaid · stub-reason). (Gaurav 2026-07-31)
    raw = invoice.ai_extraction_raw or {}
    recon = raw.get("recon") or {}
    dup = recon.get("duplicate") or {}
    _rref = raw.get("retool_ref") or {}
    result["tags"] = {
        "retool_id": _rref.get("finance_db_id"),
        "retool_created_at": _rref.get("created_at"),  # when raised in Retool finance_db
        "retool_paid_at": _rref.get("closed_at"),       # marked Closed/paid in Retool
        "provisional_paid": (raw.get("provisional_paid") or {}).get("is_provisional_paid"),
        "is_duplicate": bool(dup.get("is_duplicate", False)),
        "duplicate_of": dup.get("duplicate_of"),
        "ingest_outcome": recon.get("ingest_outcome"),  # not_invoice/no_attachment/no_file/duplicate
        "stub": bool(recon.get("stub", False)),
        # matched/quarantine derived from LIVE counterparty, not the frozen flag
        "matched": invoice.counterparty_id is not None,
    }

    # Full action-audit trail — who/when/why for every transition (migration 047).
    def _iso(dt):
        return dt.isoformat() if dt else None
    result["audit"] = {
        "uploaded_by": invoice.uploaded_by,
        "submitted_by": invoice.submitted_by, "submitted_at": _iso(invoice.submitted_at),
        "submit_override_reason": invoice.submit_override_reason,
        "approved_by": invoice.approved_by, "approved_at": _iso(invoice.approved_at),
        "rejected_by": invoice.rejected_by, "rejected_at": _iso(invoice.rejected_at),
        "rejection_reason": invoice.rejection_reason,
        "voided_by": invoice.voided_by, "voided_at": _iso(invoice.voided_at),
        "void_reason": invoice.void_reason,
    }

    return result


def _months_between(start: date, end: date) -> int:
    """Calculate the number of calendar months between two dates (inclusive)."""
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


class InvoiceService:
    """Service for managing invoices in the Accounts Payable system."""

    def find_by_pdf_hash(self, db: Session, pdf_hash: str) -> Optional[FinanceInvoice]:
        """Return an existing invoice with this PDF content hash, or None."""
        return (
            db.query(FinanceInvoice)
            .filter(FinanceInvoice.pdf_content_hash == pdf_hash)
            .first()
        )

    def _apply_filters(self, query, *, entity_id=None, status=None, counterparty_id=None,
                       search=None, vendor_flag=None, coa_flag=None, document_gate=None,
                       currency_flag=None, retool_status=None, sub_category=None, amount_match=None,
                       provisional_paid=None, retool_id=None, is_duplicate=None,
                       amount_min=None, amount_max=None, paired=None):
        """Shared filter builder for get_all + count_all (server-side, incl. ai_extraction_raw JSON)."""
        from sqlalchemy import or_, func, exists
        from src.models.invoice_payment_match import FinanceInvoicePaymentMatch

        def jtext(*path):  # column is JSONB in the DB -> use jsonb_extract_path_text
            return func.jsonb_extract_path_text(FinanceInvoice.ai_extraction_raw, *path)

        if entity_id is not None:
            query = query.filter(FinanceInvoice.entity_id == entity_id)
        # Amount range — on the invoice's own total (always positive); abs() is belt-and-braces.
        if amount_min is not None:
            query = query.filter(func.abs(FinanceInvoice.total_amount) >= float(amount_min))
        if amount_max is not None:
            query = query.filter(func.abs(FinanceInvoice.total_amount) <= float(amount_max))
        # Paired — does the invoice have ANY payment match (provisional OR logged/final)?
        if paired is not None:
            has_match = exists().where(FinanceInvoicePaymentMatch.invoice_id == FinanceInvoice.id)
            query = query.filter(has_match if str(paired).lower() in ("true", "yes", "1")
                                 else ~has_match)
        if status is not None:
            query = query.filter(FinanceInvoice.status == status)
        if counterparty_id is not None:
            query = query.filter(FinanceInvoice.counterparty_id == counterparty_id)
        if vendor_flag:
            query = query.filter(jtext("recon", "vendor_flag") == vendor_flag)
        if coa_flag:
            query = query.filter(jtext("recon", "coa_flag") == coa_flag)
        if document_gate:  # 'ok' (real invoice, incl. legacy null) or 'not_invoice'
            query = query.filter(func.coalesce(jtext("recon", "document_gate"), "ok") == document_gate)
        if currency_flag is not None:
            query = query.filter(jtext("recon", "currency_entity_flag") == str(currency_flag).lower())
        if amount_match is not None:
            query = query.filter(jtext("recon", "amount_match") == str(amount_match).lower())
        if provisional_paid is not None:
            query = query.filter(jtext("provisional_paid", "is_provisional_paid") == str(provisional_paid).lower())
        if retool_status:
            query = query.filter(jtext("retool_ref", "status") == retool_status)
        if sub_category:
            query = query.filter(jtext("retool_ref", "sub_category") == sub_category)
        if retool_id:
            query = query.filter(jtext("retool_ref", "finance_db_id") == str(retool_id))
        if is_duplicate is not None:
            query = query.filter(jtext("recon", "duplicate", "is_duplicate") == str(is_duplicate).lower())
        if search:
            like = f"%{search}%"
            conds = [
                FinanceInvoice.invoice_number.ilike(like),
                jtext("extraction", "vendor_name").ilike(like),
                jtext("retool_ref", "payee").ilike(like),
            ]
            # A purely-numeric term also matches the invoice's own DB id (search by id).
            if search.strip().isdigit():
                conds.append(FinanceInvoice.id == int(search.strip()))
            query = query.filter(or_(*conds))
        return query

    def get_all(self, db: Session, *, limit: int = 100, offset: int = 0, **filters) -> list[FinanceInvoice]:
        """Retrieve invoices with optional server-side filtering + pagination."""
        query = self._apply_filters(db.query(FinanceInvoice), **filters)
        return (query.order_by(FinanceInvoice.invoice_date.desc(), FinanceInvoice.id.desc())
                .limit(limit).offset(offset).all())

    def count_all(self, db: Session, **filters) -> int:
        """Count invoices matching the same filters (for pagination total)."""
        return self._apply_filters(db.query(FinanceInvoice), **filters).count()

    def get_by_id(self, db: Session, invoice_id: int) -> FinanceInvoice:
        """Retrieve an invoice by ID. Raises NotFoundError if missing."""
        invoice = db.get(FinanceInvoice, invoice_id)
        if not invoice:
            raise NotFoundError(f"Invoice with ID {invoice_id} not found")
        return invoice

    def create(self, db: Session, data: InvoiceCreate) -> FinanceInvoice:
        """
        Create a new invoice.

        Checks for semantic duplicates (same entity+counterparty+invoice_number+date+currency)
        before inserting. Auto-matches against contracts if counterparty is set.
        """
        # Duplicates are ALLOWED at draft (Gaurav 2026-08-01): we create the invoice and
        # FLAG it if it duplicates an earlier one; promotion is blocked later (submit gate).
        # No hard reject here — the dedup verdict is applied after insert.
        invoice = FinanceInvoice(
            entity_id=data.entity_id,
            counterparty_id=data.counterparty_id,
            contract_id=data.contract_id,
            invoice_number=data.invoice_number,
            invoice_date=data.invoice_date,
            due_date=data.due_date,
            total_amount=data.total_amount,
            net_amount=data.net_amount,
            tax_amount=data.tax_amount,
            currency=data.currency,
            service_period_start=data.service_period_start,
            service_period_end=data.service_period_end,
            uploaded_by=data.uploaded_by,
            notes=data.notes,
            pdf_s3_key=data.pdf_s3_key,
            pdf_content_hash=data.pdf_content_hash,
            new_vendor=data.new_vendor,
            status=InvoiceStatus.DRAFT.value,
        )

        # ── COA priority chain at invoice upload ────────────────────────
        # 1. Phase 4 categorization rules       (source: "rule")      ← RULES FIRST
        # 2. Contract COA                       (source: "contract")
        # 3. Counterparty default_account_code  (source: "db")
        # 4. AI suggestion                      (source: "ai")
        # ────────────────────────────────────────────────────────────
        # Manual override at approval time overrides all.
        coa_code: Optional[str] = None
        coa_source: Optional[str] = None

        # AI suggestion (used as fallback after all else)
        ai_coa = data.contra_account_code

        # 1. Phase 4 categorization rules ← Apply FIRST (rules are smarter than defaults)
        from src.services.categorization_service import categorization_service
        matched_rule = categorization_service.match_invoice_to_rule(
            db=db,
            counterparty_id=data.counterparty_id,
            counterparty_name=None,  # Will be fetched if needed
            amount=data.total_amount,
            currency=data.currency,
            description=data.notes,
        )
        if matched_rule and matched_rule.contra_account_code:
            coa_code = matched_rule.contra_account_code
            coa_source = "rule"

        # 2. Contract matching (only if no rule matched)
        if not coa_code and data.counterparty_id and not data.contract_id:
            from src.services.contract_service import contract_service
            contract = contract_service.find_for_invoice(
                db, data.counterparty_id, data.entity_id, data.total_amount, data.currency,
            )
            if contract:
                invoice.contract_id = contract.id
                invoice.contract_matched = True
                if contract.coa_account_code:
                    coa_code = contract.coa_account_code
                    coa_source = "contract"

        # 3. Counterparty default_account_code (fallback if no rule/contract)
        if not coa_code and data.counterparty_id:
            cp = db.get(FinanceCounterparty, data.counterparty_id)
            if cp and cp.default_account_code:
                coa_code = cp.default_account_code
                coa_source = "db"

        # 4. Fall back to AI suggestion
        if not coa_code and ai_coa:
            coa_code = ai_coa
            coa_source = "ai"

        invoice.contra_account_code = coa_code
        invoice.coa_source = coa_source

        db.add(invoice)
        try:
            db.commit()
        except IntegrityError:
            # active-only indexes let drafts duplicate; a violation here means a clash
            # with a LIVE (non-draft) invoice — that genuinely can't be created.
            db.rollback()
            from src.utils.errors import ConflictError
            raise ConflictError("This invoice already exists as a posted/approved record.")
        db.refresh(invoice)

        # Flag (don't reject) if this duplicates an EARLIER invoice — hash or vendor+number+amount.
        from src.services.duplicate_detection_service import duplicate_detection_service
        from sqlalchemy.orm.attributes import flag_modified
        verdict = duplicate_detection_service.detect(
            db, entity_id=invoice.entity_id, counterparty_id=invoice.counterparty_id,
            invoice_number=invoice.invoice_number, total_amount=invoice.total_amount,
            invoice_date=invoice.invoice_date, currency=invoice.currency,
            pdf_content_hash=invoice.pdf_content_hash, exclude_id=invoice.id,
        )
        if verdict.duplicate_of and verdict.duplicate_of < invoice.id:
            raw = dict(invoice.ai_extraction_raw or {})
            recon = dict(raw.get("recon") or {})
            recon["duplicate"] = {"is_duplicate": bool(verdict.is_duplicate),
                                  "duplicate_of": f"inv#{verdict.duplicate_of}",
                                  "dup_reason": verdict.level}
            raw["recon"] = recon
            invoice.ai_extraction_raw = raw
            flag_modified(invoice, "ai_extraction_raw")
            db.commit()
            db.refresh(invoice)
        return invoice

    def update(self, db: Session, invoice_id: int, data: InvoiceUpdate) -> FinanceInvoice:
        """Update an invoice. Only draft/pending_approval invoices can be edited."""
        invoice = self.get_by_id(db, invoice_id)

        if invoice.status not in (InvoiceStatus.DRAFT.value, InvoiceStatus.PENDING_APPROVAL.value):
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Cannot update invoice in '{invoice.status}' status. "
                f"Only draft or pending_approval invoices can be edited."
            )

        update_data = data.model_dump(exclude_unset=True)
        # Convert enum to string value if status was provided
        if "status" in update_data and update_data["status"] is not None:
            update_data["status"] = update_data["status"].value if hasattr(update_data["status"], "value") else update_data["status"]

        for field, value in update_data.items():
            setattr(invoice, field, value)

        # Refresh the recon flags to reflect the edit — the review UI reads these
        # (Gaurav 2026-07-31: after assigning counterparty / COA the flags must update).
        raw = dict(invoice.ai_extraction_raw or {})
        recon = dict(raw.get("recon") or {})
        recon["vendor_flag"] = "MATCHED" if invoice.counterparty_id else "QUARANTINE"
        recon["coa_flag"] = "OK" if invoice.contra_account_code else "NEEDS-COA"
        raw["recon"] = recon
        invoice.ai_extraction_raw = raw
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(invoice, "ai_extraction_raw")

        db.commit()
        db.refresh(invoice)
        return invoice

    def attach_document(self, db: Session, invoice_id: int, file_bytes: bytes,
                        filename: str = "invoice.pdf") -> tuple:
        """Attach a real document to an EXISTING invoice (typically a no-document stub),
        backfilling its fields IN PLACE — never creating a new row. (Gaurav 2026-08-01)

        Runs the same pipeline as fresh ingestion: hash → extract → vendor-match → S3 →
        backfill → clear stub markers → duplicate detect. Returns (invoice, DuplicateVerdict).
        """
        import hashlib
        from datetime import date as _date
        from sqlalchemy.orm.attributes import flag_modified
        from src.services.ai_extraction_service import ai_extraction_service
        from src.services.vendor_matching_service import vendor_matching_service
        from src.services.s3_service import s3_service
        from src.services.duplicate_detection_service import duplicate_detection_service
        from src.models.entity import FinanceEntity

        invoice = self.get_by_id(db, invoice_id)
        if invoice.status not in (InvoiceStatus.DRAFT.value, InvoiceStatus.PENDING_APPROVAL.value):
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Cannot attach a document to an invoice in '{invoice.status}' status "
                f"(only draft / pending_approval)."
            )

        h = hashlib.sha256(file_bytes).hexdigest()
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ".pdf"
        entity_names = [e.name for e in db.query(FinanceEntity).filter(FinanceEntity.status == "active").all()]
        ex = ai_extraction_service.extract_invoice_data(file_bytes, entity_names=entity_names, file_extension=ext)

        def _dpart(s):
            try:
                return _date.fromisoformat(str(s)[:10]) if s else None
            except ValueError:
                return None

        # Vendor match if the stub has no counterparty yet
        cp_id = invoice.counterparty_id
        if not cp_id and (ex.get("vendor_name") or None):
            cp, _is_new, _conf = vendor_matching_service.match_or_create(db, ex.get("vendor_name"), ex.get("vendor_tax_id"))
            cp_id = cp.id if cp else None

        # Backfill from the document (extracted values are authoritative for a real doc)
        if ex.get("invoice_number"):
            invoice.invoice_number = ex.get("invoice_number")
        d = _dpart(ex.get("invoice_date"))
        if d:
            invoice.invoice_date = d
        # Coerce extracted amounts locale-aware (US/EU/parenthesised/currency).
        # A raw assignment stores garbage in a financial column that drives
        # dedup, amortization and the AP JE. On a genuine parse failure we do
        # NOT silently skip — we SURFACE it so the review UI flags the row.
        _amount_warnings: list[str] = []
        for _fld, _key in (("total_amount", "total_amount"),
                           ("net_amount", "subtotal_amount"),
                           ("tax_amount", "tax_amount")):
            _raw = ex.get(_key)
            if _raw is not None:
                _val = _coerce_amount(_raw)
                if _val is not None:
                    setattr(invoice, _fld, _val)
                else:
                    _amount_warnings.append(f"{_key}={_raw!r}")
                    logger.warning("attach_document: unparseable %s %r for invoice %s — left unchanged",
                                   _key, _raw, invoice.id)
        if ex.get("currency"):
            invoice.currency = (ex.get("currency") or invoice.currency)[:3]
        dd = _dpart(ex.get("due_date"))
        if dd:
            invoice.due_date = dd
        invoice.counterparty_id = cp_id
        if cp_id and not invoice.contra_account_code:
            from src.models.counterparty import FinanceCounterparty
            cp = db.get(FinanceCounterparty, cp_id)
            if cp and cp.default_account_code:
                invoice.contra_account_code = cp.default_account_code
                invoice.coa_source = "db"

        invoice.pdf_s3_key = s3_service.upload_invoice_pdf(file_bytes, filename=filename, entity_id=invoice.entity_id)
        invoice.pdf_content_hash = h

        # Refresh recon: clear the stub markers, record the extraction, refresh flags
        raw = dict(invoice.ai_extraction_raw or {})
        recon = dict(raw.get("recon") or {})
        recon.pop("document_status", None)
        recon["stub"] = False
        recon["ingest_outcome"] = "attached"
        is_inv = ex.get("is_invoice")
        recon["document_gate"] = "not_invoice" if (is_inv is False or ex.get("document_type") in
                                                   ("statement", "letter", "report", "spreadsheet_screenshot")) else "ok"
        recon["vendor_flag"] = "MATCHED" if cp_id else "QUARANTINE"
        recon["coa_flag"] = "OK" if invoice.contra_account_code else ("NEEDS-COA" if cp_id else "NO-COUNTERPARTY")
        if _amount_warnings:
            recon["amount_flag"] = "UNPARSEABLE"
            recon["extraction_warnings"] = _amount_warnings
        else:
            recon.pop("amount_flag", None)
            recon.pop("extraction_warnings", None)
        raw["extraction"] = {k: ex.get(k) for k in (
            "vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date",
            "total_amount", "subtotal_amount", "tax_amount", "currency", "description",
            "suggested_coa_account", "is_invoice", "document_type", "confidence")}

        # Duplicate detection on the now-populated row
        verdict = duplicate_detection_service.detect(
            db, entity_id=invoice.entity_id, counterparty_id=cp_id,
            invoice_number=invoice.invoice_number, total_amount=invoice.total_amount,
            invoice_date=invoice.invoice_date, currency=invoice.currency,
            pdf_content_hash=h, exclude_id=invoice.id,
        )
        # Flag as duplicate only if an EARLIER invoice matches (first one wins)
        if verdict.duplicate_of and verdict.duplicate_of < invoice.id:
            recon["duplicate"] = {"is_duplicate": bool(verdict.is_duplicate),
                                  "duplicate_of": f"inv#{verdict.duplicate_of}",
                                  "dup_reason": verdict.level}
        raw["recon"] = recon
        invoice.ai_extraction_raw = raw
        flag_modified(invoice, "ai_extraction_raw")

        invoice.ai_confidence_score = ex.get("confidence")
        db.commit()
        db.refresh(invoice)
        return invoice, verdict

    def _payable_account_for(self, db: Session, contra_code: Optional[str]) -> str:
        """Resolve the credit (offset) leg for a given debit (expense/COS/asset) account.

        The offset is PURELY a function of the COA: every account carries an explicit
        offset_account_code (NOT NULL DEFAULT '2000'). The chart already segregates
        employee-facing accounts (6010-6014, 5062 -> 2303 Employee Claims Payable) and
        statutory ones (6002 -> 2302 super, 6001 -> 2300 CPF, 9000 -> 2305 income tax)
        from vendor accounts (-> 2000), so no counterparty logic is needed. (POL-77/78)
        """
        if not contra_code:
            return AP_ACCOUNT_CODE
        from src.models.account import FinanceAccount
        acct = (
            db.query(FinanceAccount)
            .filter(FinanceAccount.code == contra_code)
            .first()
        )
        return acct.offset_account_code if acct and acct.offset_account_code else AP_ACCOUNT_CODE

    def approve(self, db: Session, invoice_id: int, approved_by: str, contra_account_code: Optional[str] = None) -> FinanceInvoice:
        """
        Approve an invoice, creating the corresponding journal entry.

        Standard case: Dr contra_account / Cr 2000 (Accounts Payable)
        Amortization case: Dr 1200 (Prepaid) / Cr 2000, plus amortization schedule
        """
        invoice = self.get_by_id(db, invoice_id)

        # POL (Gaurav 2026-07-31): NO direct-to-approved. Every invoice must pass
        # through pending_approval first — a draft cannot be approved directly.
        if invoice.status != InvoiceStatus.PENDING_APPROVAL.value:
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Cannot approve invoice in '{invoice.status}' status. "
                f"Only pending_approval invoices can be approved (submit it first)."
            )

        # COA priority at approval time:
        # 1. Manual override from approver (contra_account_code parameter) — highest priority
        # 2. For VERIFIED counterparties: ALWAYS use default_account_code (ignores AI suggestion)
        # 3. For UNVERIFIED counterparties: use default if available, else AI suggestion
        from src.models.counterparty import FinanceCounterparty

        if contra_account_code:
            # Approver explicitly provided a COA
            invoice.contra_account_code = contra_account_code
            invoice.coa_source = "manual"
        elif invoice.counterparty_id:
            counterparty = db.get(FinanceCounterparty, invoice.counterparty_id)
            if counterparty:
                if counterparty.is_verified:
                    # Verified counterparties MUST have a default COA
                    # Always use it, ignoring any AI suggestion
                    if counterparty.default_account_code:
                        invoice.contra_account_code = counterparty.default_account_code
                        invoice.coa_source = "db"
                    else:
                        # Should not happen — verified vendors must have COA
                        from src.utils.errors import BadRequestError
                        raise BadRequestError(
                            f"Verified vendor '{counterparty.name}' is missing default_account_code. "
                            f"Update vendor configuration before approving invoices."
                        )
                else:
                    # Unverified/auto-created counterparty
                    # Use default if available, else AI suggestion is acceptable
                    if counterparty.default_account_code:
                        invoice.contra_account_code = counterparty.default_account_code
                        invoice.coa_source = "db"
                    # else: keep AI suggestion (coa_source = 'ai')

        if not invoice.contra_account_code:
            from src.utils.errors import BadRequestError
            raise BadRequestError(
                "Cannot approve invoice without a contra_account_code. "
                "For pre-registered vendors, update their default_account_code. "
                "For new vendors, set default_account_code or provide COA in approval request."
            )

        total = float(invoice.total_amount)
        tax = float(invoice.tax_amount) if invoice.tax_amount else 0.0
        # POL-87: GST posts ONLY for GST-registered entities (entity.gst_rate set).
        # For a non-registered entity (e.g. the SG entities) the GST is non-recoverable
        # and must be expensed as cost — NO 1350 line — even if the invoice carries an
        # extracted tax amount. This per-entity rule is shared with the categorization
        # engine (both read finance_entities.gst_rate).
        from src.models.entity import FinanceEntity
        _entity = db.get(FinanceEntity, invoice.entity_id)
        _gst_registered = bool(_entity and _entity.gst_rate and float(_entity.gst_rate) > 0)
        if not _gst_registered:
            tax = 0.0
        # Guard against inconsistent extraction (tax must be a sane fraction of total).
        if tax < 0 or tax >= total:
            tax = 0.0
        # net is DERIVED from total so the bill ALWAYS balances: total is authoritative
        # (it is what we owe and what the payment settles). A stored net_amount that
        # disagrees with total-tax is bad extraction and is ignored for the JE. (2026-08-03)
        net = round(total - tax, 2)

        needs_amortization = (
            invoice.service_period_start
            and invoice.service_period_end
            and _months_between(invoice.service_period_start, invoice.service_period_end) > 1
        )

        if needs_amortization:
            debit_code = PREPAID_ACCOUNT_CODE
        else:
            debit_code = invoice.contra_account_code

        # Credit leg: dedicated liability if the chosen expense account declares one
        # (e.g. 6002 super -> 2302 payable), else generic 2000 AP. Resolve from the
        # real expense (invoice.contra_account_code), not the prepaid substitute.
        credit_code = self._payable_account_for(db, invoice.contra_account_code)

        inv_ref = f"Invoice {invoice.invoice_number or invoice.id}"

        if tax > 0:
            # 3-line GST JE: Dr expense (net) + Dr 1350 GST Input (tax) / Cr AP (total)
            lines = [
                {
                    "account_code": debit_code,
                    "debit_amount": round(net, 2),
                    "credit_amount": 0.0,
                    "description": inv_ref,
                },
                {
                    "account_code": GST_INPUT_ACCOUNT_CODE,
                    "debit_amount": round(tax, 2),
                    "credit_amount": 0.0,
                    "description": f"GST Input Tax - {inv_ref}",
                },
                {
                    "account_code": credit_code,
                    "debit_amount": 0.0,
                    "credit_amount": round(total, 2),
                    "description": inv_ref,
                },
            ]
        else:
            # Standard 2-line JE: Dr expense / Cr payable (dedicated liability or 2000 AP)
            lines = [
                {
                    "account_code": debit_code,
                    "debit_amount": total,
                    "credit_amount": 0.0,
                    "description": inv_ref,
                },
                {
                    "account_code": credit_code,
                    "debit_amount": 0.0,
                    "credit_amount": total,
                    "description": inv_ref,
                },
            ]

        entry = journal_service.create(
            db=db,
            entity_id=invoice.entity_id,
            entry_date=invoice.invoice_date,
            description=f"AP Invoice: {invoice.invoice_number or f'#{invoice.id}'}",
            lines=lines,
        )
        entry.source = "invoice_approval"
        entry.reference_number = f"INV-{invoice.id}"  # trace JE -> invoice (Gaurav 2026-08-03)
        db.flush()

        invoice.journal_entry_id = entry.id
        invoice.approved_by = approved_by
        invoice.approved_at = datetime.now(UTC)
        invoice.status = InvoiceStatus.APPROVED.value

        # Create amortization schedule if needed
        if needs_amortization:
            months = _months_between(invoice.service_period_start, invoice.service_period_end)
            monthly_amount = round(total / months, 2)
            schedule = FinanceAmortizationSchedule(
                invoice_id=invoice.id,
                total_amount=total,
                months=months,
                monthly_amount=monthly_amount,
                expense_account_code=invoice.contra_account_code,
                prepaid_account_code=PREPAID_ACCOUNT_CODE,
                start_month=invoice.service_period_start.replace(day=1),
            )
            db.add(schedule)
            invoice.has_amortization_schedule = True

        db.commit()
        db.refresh(invoice)

        # ── Retroactive knock-off: find existing bank payments for this invoice ──
        # Runs best-effort after commit. Errors are logged but do not fail the approval.
        try:
            self.run_retroactive_knockoff(db, invoice)
        except Exception as e:
            logger.error(
                f"Retroactive knock-off failed for invoice {invoice.id}: {e}",
                exc_info=True,
            )

        db.refresh(invoice)
        return invoice

    def reject(self, db: Session, invoice_id: int, rejection_reason: str,
               rejected_by: Optional[str] = None) -> FinanceInvoice:
        """Reject an invoice with a reason. Captures who/when (rejected_by = logged-in user)."""
        invoice = self.get_by_id(db, invoice_id)

        if invoice.status not in (InvoiceStatus.DRAFT.value, InvoiceStatus.PENDING_APPROVAL.value):
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Cannot reject invoice in '{invoice.status}' status."
            )

        invoice.status = InvoiceStatus.REJECTED.value
        invoice.rejection_reason = rejection_reason
        invoice.rejected_by = rejected_by
        invoice.rejected_at = datetime.now(UTC)
        db.commit()
        db.refresh(invoice)
        return invoice

    def void(self, db: Session, invoice_id: int, voided_by: Optional[str] = None,
             void_reason: Optional[str] = None) -> FinanceInvoice:
        """Void an invoice. Only draft, pending_approval, or rejected invoices can be voided.
        Captures who/when/why for traceability (voided_by = logged-in user)."""
        invoice = self.get_by_id(db, invoice_id)

        allowed = (
            InvoiceStatus.DRAFT.value,
            InvoiceStatus.PENDING_APPROVAL.value,
            InvoiceStatus.REJECTED.value,
        )
        if invoice.status not in allowed:
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Cannot void invoice in '{invoice.status}' status. "
                f"Only draft, pending_approval, or rejected invoices can be voided."
            )

        invoice.status = InvoiceStatus.VOID.value
        invoice.voided_by = voided_by
        invoice.voided_at = datetime.now(UTC)
        invoice.void_reason = void_reason
        db.commit()
        db.refresh(invoice)
        return invoice

    def get_open_for_counterparty(
        self,
        db: Session,
        counterparty_id: int,
        amount: float,
        currency: str,
        description: str = "",
        reference_number: str = "",
        transaction_date: Optional[date] = None,
    ) -> Optional[FinanceInvoice]:
        """
        Find matching invoice for AP knock-off using 3-case framework.

        CASE 1: Reference + Amount + Date (DEFINITIVE)
          IF invoice_number in description/reference AND amount ≈ remaining (±2%) AND txn_date > invoice_date
          → Return invoice (use invoice.account_code)

        CASE 2: Amount + Date, NO Reference (FIFO)
          IF no invoice_number in description AND amount ≈ remaining (±2%) AND txn_date > invoice_date
          → Return FIRST (oldest) matching invoice (use invoice.account_code)

        CASE 3: Amount doesn't match any invoice
          → Return None (calling code will use 1300 Prepayments asset account)

        Returns None if no match found or no invoices exist for counterparty.
        """
        open_statuses = (InvoiceStatus.APPROVED.value, InvoiceStatus.PARTIALLY_PAID.value)

        query = (
            db.query(FinanceInvoice)
            .filter(
                FinanceInvoice.counterparty_id == counterparty_id,
                FinanceInvoice.currency == currency,
                FinanceInvoice.status.in_(open_statuses),
            )
        )
        if transaction_date is not None:
            query = query.filter(FinanceInvoice.invoice_date <= transaction_date)

        invoices = query.order_by(FinanceInvoice.invoice_date.asc(), FinanceInvoice.id.asc()).all()

        desc_upper = (description or "").upper()
        ref_upper = (reference_number or "").upper()

        # CASE 1: Reference + Amount + Date (DEFINITIVE)
        for inv in invoices:
            remaining = float(inv.total_amount) - float(inv.amount_paid)
            if remaining <= 0:
                continue
            if inv.invoice_number:
                inv_num_upper = inv.invoice_number.upper()
                if inv_num_upper and (
                    inv_num_upper in desc_upper or inv_num_upper in ref_upper
                ):
                    # Found reference; check amount (±2% FX tolerance)
                    if abs(amount - remaining) <= remaining * 0.02:
                        return inv

        # CASE 2: Amount + Date (NO REFERENCE) → FIFO
        for inv in invoices:
            remaining = float(inv.total_amount) - float(inv.amount_paid)
            if remaining <= 0:
                continue
            # Ensure invoice_number NOT in description (skip if already handled in CASE 1)
            if inv.invoice_number:
                inv_num_upper = inv.invoice_number.upper()
                if inv_num_upper in desc_upper or inv_num_upper in ref_upper:
                    continue  # Skip; would have been matched in CASE 1
            # Match on amount only; return FIRST (oldest)
            if abs(amount - remaining) <= remaining * 0.02:
                return inv

        # CASE 3: No match → Return None
        # Calling code will use 1300 Prepayments instead
        return None

    def get_open_for_match(
        self,
        db: Session,
        counterparty_id: int,
        currency: str,
        transaction_date: Optional[date] = None,
    ) -> list[FinanceInvoice]:
        """
        Return all open invoices for a counterparty that are eligible for manual matching.

        Ordered oldest-first. Optionally filtered to invoices dated on or before
        transaction_date (same date-constraint as auto-match).
        """
        open_statuses = (InvoiceStatus.APPROVED.value, InvoiceStatus.PARTIALLY_PAID.value)

        query = (
            db.query(FinanceInvoice)
            .filter(
                FinanceInvoice.counterparty_id == counterparty_id,
                FinanceInvoice.currency == currency,
                FinanceInvoice.status.in_(open_statuses),
            )
        )
        if transaction_date is not None:
            query = query.filter(FinanceInvoice.invoice_date <= transaction_date)

        return query.order_by(FinanceInvoice.invoice_date.asc(), FinanceInvoice.id.asc()).all()

    def match_transaction(
        self,
        db: Session,
        invoice_id: int,
        transaction_id: int,
        matched_by: str = "manual",
    ) -> dict:
        """
        Manually match a bank transaction against an open invoice.

        Performs the same work as the auto AP knock-off:
          - Creates payment JE: Dr 2000 AP / Cr bank_coa_code
          - Calls record_payment to update invoice.amount_paid and status
          - Marks transaction → MATCHED, links JE

        Raises BadRequestError if the transaction is already matched, not outgoing,
        or the invoice is not open.
        Raises NotFoundError if either record does not exist.
        """
        from datetime import datetime, UTC
        from src.models.transaction import FinanceTransaction, TransactionStatus
        from src.models.bank_account import FinanceBankAccount
        from src.utils.errors import BadRequestError
        from src.services.journal_service import journal_service

        invoice = self.get_by_id(db, invoice_id)
        txn = db.get(FinanceTransaction, transaction_id)
        if not txn:
            raise NotFoundError(f"Transaction with ID {transaction_id} not found")

        open_statuses = (InvoiceStatus.APPROVED.value, InvoiceStatus.PARTIALLY_PAID.value)
        if invoice.status not in open_statuses:
            raise BadRequestError(
                f"Invoice {invoice_id} is not open for payment (status: {invoice.status})."
            )

        if txn.status == TransactionStatus.MATCHED:
            raise BadRequestError(
                f"Transaction {transaction_id} is already matched."
            )

        amount = float(txn.amount) if txn.amount is not None else 0.0
        if amount >= 0:
            raise BadRequestError(
                "Only outgoing payments (negative amount) can be matched against AP invoices."
            )

        abs_amount = abs(amount)
        remaining = float(invoice.total_amount) - float(invoice.amount_paid)
        if remaining <= 0:
            raise BadRequestError(
                f"Invoice {invoice_id} has no remaining balance."
            )
        if abs_amount > remaining * 1.02:
            raise BadRequestError(
                f"Payment amount {abs_amount} exceeds invoice remaining balance "
                f"{remaining:.2f} (>2% over). Use a credit note for overpayments."
            )

        bank_account = db.get(FinanceBankAccount, txn.bank_account_id)
        if not bank_account or not bank_account.coa_account_code:
            raise BadRequestError(
                f"Bank account for transaction {transaction_id} has no COA code set."
            )

        inv_ref = f"Invoice {invoice.invoice_number or invoice.id}"
        entry = self.create_ap_payment_entries(
            db=db,
            bank_account=bank_account,
            invoice=invoice,
            txn_date=txn.transaction_date,
            abs_amount=abs_amount,
            source="ap_manual_match",
            description=f"AP Payment ({matched_by}): {inv_ref}",
        )

        self.record_payment(db, invoice.id, abs_amount)

        now = datetime.now(UTC)
        txn.status = TransactionStatus.MATCHED
        txn.reconciled_journal_entry_id = entry.id
        txn.matched_at = now
        db.commit()

        db.refresh(invoice)
        db.refresh(txn)
        return {
            "invoice_id": invoice.id,
            "transaction_id": txn.id,
            "journal_entry_id": entry.id,
            "cross_entity": bank_account.entity_id != invoice.entity_id,
            "amount_applied": abs_amount,
            "invoice_status": invoice.status,
        }

    # ── Retroactive knock-off (System 2 / Step 2.1) ──────────────────────────

    def _reopen_transaction(
        self,
        db: Session,
        txn: "FinanceTransaction",
        reason: str,
    ) -> None:
        """
        Void the transaction's current JE and reset it to PENDING.

        System-driven only — not user-initiated. Used by retroactive knock-off
        when a payment was already matched/reconciled as a direct expense but
        an invoice has now arrived that should settle it via AP instead.

        Writes reopen_reason and reopened_at for audit trail.
        """
        from src.models.transaction import TransactionStatus
        from datetime import datetime, UTC

        if txn.reconciled_journal_entry_id:
            journal_service.void_entry(
                db, txn.reconciled_journal_entry_id,
                reason=f"retroactive_ap_knockoff: {reason}",
            )

        txn.status = TransactionStatus.PENDING
        txn.reconciled_journal_entry_id = None
        txn.matched_at = None
        txn.reconciled_at = None
        txn.reopen_reason = reason
        txn.reopened_at = datetime.now(UTC)
        db.flush()

    def run_retroactive_knockoff(
        self,
        db: Session,
        invoice: FinanceInvoice,
    ) -> list[dict]:
        """
        After an invoice is approved, search for existing bank transactions that
        look like payments for it and knock them off against the new AP liability.

        Called automatically at the end of approve(). Safe to call multiple times
        (skips already-AP-matched transactions).

        Search criteria:
        - counterparty_id matches invoice
        - currency matches invoice
        - amount is negative (outgoing payment)
        - amount fits: Tier 1 reference / Tier 2 exact / Tier 3 partial
        - transaction_date within ±30 days of invoice_date

        Per-transaction handling:
        - PENDING    → knock off directly
        - MATCHED    → void existing JE, reopen to PENDING, knock off
        - RECONCILED → void existing JE, reopen to PENDING, knock off
        - Any status with existing JE from prior AP knock-off → skip (conflict)

        Returns a list of result dicts (one per transaction touched).
        """
        from src.models.transaction import FinanceTransaction, TransactionStatus
        from src.models.bank_account import FinanceBankAccount
        from src.models.journal_entry import FinanceJournalEntry
        from datetime import timedelta, datetime, UTC
        from sqlalchemy import or_

        AP_SOURCES = {"ap_knockoff", "ap_manual_match"}

        if not invoice.counterparty_id:
            return []

        remaining = float(invoice.total_amount) - float(invoice.amount_paid)
        if remaining <= 0:
            return []

        date_low = invoice.invoice_date - timedelta(days=30)
        date_high = invoice.invoice_date + timedelta(days=30)

        candidates = (
            db.query(FinanceTransaction)
            .filter(
                FinanceTransaction.counterparty_id == invoice.counterparty_id,
                FinanceTransaction.currency == invoice.currency,
                FinanceTransaction.amount < 0,
                FinanceTransaction.transaction_date.between(date_low, date_high),
                FinanceTransaction.status.in_([
                    TransactionStatus.PENDING,
                    TransactionStatus.MATCHED,
                    TransactionStatus.RECONCILED,
                ]),
            )
            .order_by(FinanceTransaction.transaction_date.asc(), FinanceTransaction.id.asc())
            .all()
        )

        # Filter out any that are already AP-settled (linked to an AP JE)
        eligible = []
        for txn in candidates:
            if txn.reconciled_journal_entry_id:
                je = db.get(FinanceJournalEntry, txn.reconciled_journal_entry_id)
                if je and getattr(je, "source", None) in AP_SOURCES:
                    continue  # already settled via AP — skip
            eligible.append(txn)

        if not eligible:
            return []

        # Apply ranked matching to pick the best candidate(s)
        # (same three tiers as forward knock-off, but we loop until invoice is paid)
        inv_ref = f"Invoice {invoice.invoice_number or invoice.id}"
        inv_num_upper = (invoice.invoice_number or "").upper()
        results = []

        def _score(txn) -> int:
            """Return tier rank (lower = better). 99 = no match."""
            abs_amt = abs(float(txn.amount))
            rem = float(invoice.total_amount) - float(invoice.amount_paid)
            if rem <= 0:
                return 99
            desc_upper = (txn.description or "").upper()
            ref_upper = (txn.reference_number or "").upper()
            if inv_num_upper and (inv_num_upper in desc_upper or inv_num_upper in ref_upper):
                return 1
            if abs(abs_amt - rem) <= rem * 0.02:
                return 2
            if 0 < abs_amt < rem * 1.02:
                return 3
            return 99

        sorted_candidates = sorted(eligible, key=lambda t: (_score(t), t.transaction_date, t.id))

        for txn in sorted_candidates:
            tier = _score(txn)
            if tier == 99:
                continue
            remaining_now = float(invoice.total_amount) - float(invoice.amount_paid)
            if remaining_now <= 0:
                break

            abs_amount = abs(float(txn.amount))
            apply_amount = min(abs_amount, remaining_now)

            bank_account = db.get(FinanceBankAccount, txn.bank_account_id)
            if not bank_account or not bank_account.coa_account_code:
                results.append({
                    "transaction_id": txn.id,
                    "status": "skipped",
                    "reason": "bank account has no COA code",
                })
                continue

            prior_status = txn.status.value

            # Reopen MATCHED or RECONCILED transactions before knocking off
            if txn.status != TransactionStatus.PENDING:
                self._reopen_transaction(
                    db, txn,
                    reason=f"invoice_{invoice.id}_retroactive_knockoff",
                )

            # Create AP payment JE(s): Dr 2000 AP / Cr Bank (or IC pair)
            entry = self.create_ap_payment_entries(
                db=db,
                bank_account=bank_account,
                invoice=invoice,
                txn_date=txn.transaction_date,
                abs_amount=apply_amount,
                source="ap_knockoff",
                description=f"AP Payment (retroactive): {inv_ref}",
            )

            self.record_payment(db, invoice.id, apply_amount)

            now = datetime.now(UTC)
            txn.status = TransactionStatus.MATCHED
            txn.reconciled_journal_entry_id = entry.id
            txn.matched_at = now
            db.commit()

            results.append({
                "transaction_id": txn.id,
                "prior_status": prior_status,
                "amount_applied": apply_amount,
                "journal_entry_id": entry.id,
                "tier": tier,
                "cross_entity": bank_account.entity_id != invoice.entity_id,
                "invoice_status": invoice.status,
            })

        return results

    # ── Cross-entity AP helpers ───────────────────────────────────────────────

    def _get_ic_codes(
        self,
        db: Session,
        bank_entity_id: int,
        invoice_entity_id: int,
    ) -> Optional[tuple[str, str]]:
        """
        Return (ic_receivable_code, ic_payable_code) for a cross-entity AP payment.

        ic_receivable_code: used in the *bank entity* books (Dr — asset increasing)
        ic_payable_code:    used in the *invoice entity* books (Cr — liability increasing)

        Returns None if the entity pair is not in the lookup table (unsupported combination).
        """
        from src.models.entity import FinanceEntity

        bank_entity = db.get(FinanceEntity, bank_entity_id)
        invoice_entity = db.get(FinanceEntity, invoice_entity_id)
        if not bank_entity or not invoice_entity:
            return None

        bank_short = _entity_short(bank_entity.name)
        inv_short = _entity_short(invoice_entity.name)

        rec_code = _IC_RECEIVABLE_CODES.get((bank_short, inv_short))
        pay_code = _IC_PAYABLE_CODES.get((inv_short, bank_short))

        if not rec_code or not pay_code:
            logger.warning(
                f"No IC codes for entity pair (bank={bank_short}, invoice={inv_short}). "
                f"Cross-entity AP knock-off skipped."
            )
            return None
        return rec_code, pay_code

    def create_ap_payment_entries(
        self,
        db: Session,
        bank_account: "FinanceBankAccount",
        invoice: FinanceInvoice,
        txn_date: date,
        abs_amount: float,
        source: str,
        description: str,
    ) -> "FinanceJournalEntry":
        """
        Create the AP payment journal entry (or entries for cross-entity).

        Same entity:
          Bank entity JE — Dr 2000 AP / Cr Bank

        Cross-entity (bank_account.entity_id ≠ invoice.entity_id):
          Bank entity JE  — Dr IC Receivable / Cr Bank
          Invoice entity JE — Dr 2000 AP / Cr IC Payable
          Both JEs share an intercompany_group_id.

        Returns the *primary* JE (always the bank entity JE).
        Raises ValueError if cross-entity codes cannot be resolved.
        """
        import uuid
        from src.models.journal_entry import FinanceJournalEntry

        bank_entity_id = bank_account.entity_id
        invoice_entity_id = invoice.entity_id
        bank_coa = bank_account.coa_account_code
        inv_ref = f"Invoice {invoice.invoice_number or invoice.id}"

        # Clear the SAME liability the approval JE credited — dedicated (e.g. 2302
        # Superannuation Payable) or generic 2000 AP — else the sub-payable never closes.
        ap_code = self._payable_account_for(db, invoice.contra_account_code)

        if bank_entity_id == invoice_entity_id:
            # ── Same-entity: single 2-line JE ──────────────────────────────
            entry = journal_service.create(
                db=db,
                entity_id=bank_entity_id,
                entry_date=txn_date,
                description=description,
                lines=[
                    {
                        "account_code": ap_code,
                        "debit_amount": abs_amount,
                        "credit_amount": 0.0,
                        "description": inv_ref,
                    },
                    {
                        "account_code": bank_coa,
                        "debit_amount": 0.0,
                        "credit_amount": abs_amount,
                        "description": inv_ref,
                    },
                ],
            )
            entry.source = source
            entry.reference_number = f"INV-{invoice.id}"  # trace JE -> invoice
            db.flush()
            return entry

        # ── Cross-entity: two paired JEs ────────────────────────────────────
        ic_codes = self._get_ic_codes(db, bank_entity_id, invoice_entity_id)
        if not ic_codes:
            raise ValueError(
                f"Cannot create cross-entity AP payment: no IC codes found "
                f"for bank entity {bank_entity_id} / invoice entity {invoice_entity_id}."
            )
        ic_receivable, ic_payable = ic_codes
        ic_group_id = str(uuid.uuid4())

        # Bank entity: Dr IC Receivable / Cr Bank
        bank_entry = journal_service.create(
            db=db,
            entity_id=bank_entity_id,
            entry_date=txn_date,
            description=description,
            lines=[
                {
                    "account_code": ic_receivable,
                    "debit_amount": abs_amount,
                    "credit_amount": 0.0,
                    "description": inv_ref,
                },
                {
                    "account_code": bank_coa,
                    "debit_amount": 0.0,
                    "credit_amount": abs_amount,
                    "description": inv_ref,
                },
            ],
        )
        bank_entry.source = source
        bank_entry.reference_number = f"INV-{invoice.id}"  # trace JE -> invoice
        bank_entry.intercompany_group_id = ic_group_id

        # Invoice entity: Dr AP (dedicated liability or 2000) / Cr IC Payable
        inv_entry = journal_service.create(
            db=db,
            entity_id=invoice_entity_id,
            entry_date=txn_date,
            description=description,
            lines=[
                {
                    "account_code": ap_code,
                    "debit_amount": abs_amount,
                    "credit_amount": 0.0,
                    "description": inv_ref,
                },
                {
                    "account_code": ic_payable,
                    "debit_amount": 0.0,
                    "credit_amount": abs_amount,
                    "description": inv_ref,
                },
            ],
        )
        inv_entry.source = source
        inv_entry.reference_number = f"INV-{invoice.id}"  # trace JE -> invoice
        inv_entry.intercompany_group_id = ic_group_id

        db.flush()
        return bank_entry

    def statement_for_counterparty(
        self,
        db: Session,
        counterparty_id: int,
        entity_id: Optional[int] = None,
    ) -> dict:
        """
        Build a vendor-level Statement of Account for a counterparty.

        Queried straight off finance_invoices + finance_counterparties.

        Money totals EXCLUDE rows gated as `not_invoice`
        (ai_extraction_raw->recon->>document_gate == 'not_invoice').

        Paid-date signal (current state): all ingested invoices are draft /
        amount_paid=0, so the paid-date comes from Retool's provisional close:
          ai_extraction_raw->provisional_paid->>provisional_paid_at  (timestamp str)
          ai_extraction_raw->provisional_paid->>is_provisional_paid  ('true'/'false')
        A future bank-confirmed paid_date (from reconciliation) supersedes this —
        see `_line_paid_date` where the precedence lives.
        """
        from sqlalchemy import func

        counterparty = db.get(FinanceCounterparty, counterparty_id)
        if not counterparty:
            raise NotFoundError(f"Counterparty {counterparty_id} not found")

        def jtext(*path):  # JSONB column -> text extraction
            return func.jsonb_extract_path_text(FinanceInvoice.ai_extraction_raw, *path)

        query = db.query(FinanceInvoice).filter(
            FinanceInvoice.counterparty_id == counterparty_id
        )
        if entity_id is not None:
            query = query.filter(FinanceInvoice.entity_id == entity_id)

        invoices = query.order_by(
            FinanceInvoice.invoice_date.asc(), FinanceInvoice.id.asc()
        ).all()

        today = date.today()

        def _gate(inv: FinanceInvoice) -> str:
            raw = inv.ai_extraction_raw or {}
            recon = raw.get("recon") or {}
            return (recon.get("document_gate") or "ok")

        def _is_not_invoice(inv: FinanceInvoice) -> bool:
            return _gate(inv) == "not_invoice"

        def _prov(inv: FinanceInvoice) -> dict:
            raw = inv.ai_extraction_raw or {}
            return raw.get("provisional_paid") or {}

        def _is_provisionally_paid(inv: FinanceInvoice) -> bool:
            return str(_prov(inv).get("is_provisional_paid")).lower() == "true"

        def _provisional_paid_at(inv: FinanceInvoice):
            return _prov(inv).get("provisional_paid_at") or None

        def _line_paid_date(inv: FinanceInvoice):
            """Bank-confirmed paid date supersedes the provisional one.

            Real reconciliation isn't wired yet (all invoices amount_paid=0),
            so for now this resolves to the provisional close date. When
            reconciliation lands, prefer the matched transaction date here.
            """
            # Future: if inv.amount_paid > 0 -> derive from matched transaction.
            return _provisional_paid_at(inv)

        # ── Aggregates (real, non-not_invoice rows only) ──────────────────
        outstanding = 0.0
        provisionally_paid_total = 0.0
        invoice_count = 0
        not_invoice_count = 0
        oldest_unpaid_date = None
        currency_breakdown: dict[str, float] = {}
        aging = {"current": 0.0, "d1_30": 0.0, "d31_60": 0.0, "d61_90": 0.0, "d90_plus": 0.0}

        for inv in invoices:
            if _is_not_invoice(inv):
                not_invoice_count += 1
                continue

            invoice_count += 1
            total = float(inv.total_amount or 0)
            paid = float(inv.amount_paid or 0)
            remaining = total - paid
            prov_paid = _is_provisionally_paid(inv)

            if prov_paid:
                provisionally_paid_total += total

            # Outstanding = balance on real invoices NOT provisionally-paid.
            if not prov_paid and remaining > 0:
                outstanding += remaining
                cur = inv.currency or "?"
                currency_breakdown[cur] = round(currency_breakdown.get(cur, 0.0) + remaining, 2)

                if oldest_unpaid_date is None or inv.invoice_date < oldest_unpaid_date:
                    oldest_unpaid_date = inv.invoice_date

                # Aging bucket by due_date (fall back to invoice_date) vs today.
                ref_date = inv.due_date or inv.invoice_date
                days_overdue = (today - ref_date).days
                if days_overdue <= 0:
                    aging["current"] += remaining
                elif days_overdue <= 30:
                    aging["d1_30"] += remaining
                elif days_overdue <= 60:
                    aging["d31_60"] += remaining
                elif days_overdue <= 90:
                    aging["d61_90"] += remaining
                else:
                    aging["d90_plus"] += remaining

        aging = {k: round(v, 2) for k, v in aging.items()}

        # ── Statement lines (chronological; running balance) ──────────────
        # One "invoice" (billed) row per invoice; a "payment" row when a
        # provisional paid date exists. Sort all events by date, then compute
        # a running balance. not_invoice rows are shown but do not move money.
        events = []
        for inv in invoices:
            not_inv = _is_not_invoice(inv)
            total = float(inv.total_amount or 0)
            events.append({
                "sort_date": inv.invoice_date,
                "seq": 0,  # billed before payment on same date
                "line": {
                    "date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                    "type": "invoice",
                    "invoice_id": inv.id,
                    "invoice_number": inv.invoice_number,
                    "entity_id": inv.entity_id,
                    "billed": total if not not_inv else 0.0,
                    "paid": 0.0,
                    "status": inv.status,
                    "currency": inv.currency,
                    "document_gate": _gate(inv),
                    "is_not_invoice": not_inv,
                },
            })

            paid_at = _line_paid_date(inv)
            if paid_at and not not_inv:
                # Parse the timestamp string just for sorting; keep raw value in output.
                sort_dt = inv.invoice_date
                try:
                    sort_dt = datetime.fromisoformat(str(paid_at).replace("Z", "+00:00")).date()
                except (ValueError, TypeError):
                    pass
                events.append({
                    "sort_date": sort_dt,
                    "seq": 1,
                    "line": {
                        "date": paid_at,
                        "type": "payment",
                        "invoice_id": inv.id,
                        "invoice_number": inv.invoice_number,
                        "entity_id": inv.entity_id,
                        "billed": 0.0,
                        "paid": total,
                        "status": inv.status,
                        "currency": inv.currency,
                        "provisional": True,
                        "note": "Retool provisional",
                    },
                })

        events.sort(key=lambda e: (e["sort_date"] or date.min, e["seq"]))

        running = 0.0
        lines = []
        for ev in events:
            line = ev["line"]
            running += float(line.get("billed", 0.0)) - float(line.get("paid", 0.0))
            line["balance"] = round(running, 2)
            lines.append(line)

        return {
            "counterparty": {
                "id": counterparty.id,
                "name": counterparty.name,
                "type": counterparty.type,
                "tax_registration_number": counterparty.tax_registration_number,
                "default_account_code": counterparty.default_account_code,
                "entity_id": counterparty.entity_id,
                "is_verified": counterparty.is_verified,
                "currency": counterparty.currency,
                "payment_terms_days": counterparty.payment_terms_days,
            },
            "summary": {
                "outstanding": round(outstanding, 2),
                "provisionally_paid_total": round(provisionally_paid_total, 2),
                "invoice_count": invoice_count,
                "not_invoice_count": not_invoice_count,
                "oldest_unpaid_date": oldest_unpaid_date.isoformat() if oldest_unpaid_date else None,
                "currency_breakdown": currency_breakdown,
            },
            "aging": aging,
            "lines": lines,
        }

    def record_payment(self, db: Session, invoice_id: int, amount_paid: float) -> FinanceInvoice:
        """
        Record a payment against an invoice.

        Updates amount_paid and transitions status to paid or partially_paid.
        """
        invoice = self.get_by_id(db, invoice_id)

        new_paid = float(invoice.amount_paid) + amount_paid
        invoice.amount_paid = round(new_paid, 2)

        total = float(invoice.total_amount)
        if new_paid >= total:
            invoice.status = InvoiceStatus.PAID.value
        else:
            invoice.status = InvoiceStatus.PARTIALLY_PAID.value

        db.commit()
        db.refresh(invoice)
        return invoice


    def submit(self, db: Session, invoice_id: int, confirmed: bool = False,
               submitted_by: Optional[str] = None, override_reason: Optional[str] = None) -> dict:
        """
        Submit a draft invoice for approval.

        Phase 1 — Validation:
          Checks entity_id, counterparty_id, contra_account_code are all set.

        Phase 2 — Approval Rules:
          Evaluates active rules ordered by priority.
          - new_vendor or coa_source='ai'/null → always pending_approval
          - Otherwise: first matching rule wins (auto_approve or require_approval)
          - No match → defaults to pending_approval
          - auto_approve → status = approved, JE created.
          - require_approval or no match → status = pending_approval.
        """
        invoice = self.get_by_id(db, invoice_id)

        if invoice.status != InvoiceStatus.DRAFT.value:
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Only draft invoices can be submitted. Current status: {invoice.status}"
            )

        # --- Phase 1: field validation ---
        missing = []
        if not invoice.entity_id:
            missing.append("entity_id")
        if not invoice.counterparty_id:
            missing.append("counterparty_id")
        if not invoice.contra_account_code:
            missing.append("contra_account_code (expense account)")
        # Posting is dated on invoice_date — block the 1900 unknown-date sentinel (backfill
        # stubs). A real invoice_date is required before an invoice can post. (Gaurav 2026-07-31)
        from datetime import date as _date
        if not invoice.invoice_date or invoice.invoice_date <= _date(1901, 1, 1):
            missing.append("invoice_date (a real date — this stub carries the 1900 unknown-date sentinel)")
        if missing:
            from src.utils.errors import BadRequestError
            raise BadRequestError(
                f"Cannot submit invoice — missing required fields: {', '.join(missing)}"
            )

        # --- HARD BLOCK (Gaurav 2026-08-01): a duplicate cannot be promoted past draft —
        # "first one wins". The deterministic layers (identical file, or same vendor +
        # invoice number + amount) block; same-number-different-amount or numberless
        # same-amount only warns (surfaced elsewhere as review, not blocked here). ---
        from src.services.duplicate_detection_service import duplicate_detection_service
        dup = duplicate_detection_service.detect(
            db,
            entity_id=invoice.entity_id,
            counterparty_id=invoice.counterparty_id,
            invoice_number=invoice.invoice_number,
            total_amount=invoice.total_amount,
            invoice_date=invoice.invoice_date,
            currency=invoice.currency,
            pdf_content_hash=invoice.pdf_content_hash,
            exclude_id=invoice.id,
        )
        # "first one wins": block only if the matched original was ingested BEFORE this
        # one (lower id). If THIS is the earliest, it is the original and may proceed.
        if dup.is_duplicate and dup.duplicate_of and dup.duplicate_of < invoice.id:
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Duplicate of invoice #{dup.duplicate_of} ({dup.level}). {dup.reason} "
                f"A duplicate cannot be promoted past draft (first one wins)."
            )

        # --- SOFT BLOCK (Gaurav 2026-07-31): submitting a doc flagged NOT-an-invoice
        # requires an explicit reason. Not a hard stop — they may proceed WITH a reason. ---
        recon = (invoice.ai_extraction_raw or {}).get("recon") or {}
        is_not_invoice = (recon.get("document_gate") == "not_invoice"
                          or recon.get("ingest_outcome") == "not_invoice")
        if is_not_invoice and not (override_reason and override_reason.strip()):
            from src.utils.errors import BadRequestError
            raise BadRequestError(
                "This document is flagged as NOT an invoice. Provide a reason to submit it "
                "for approval anyway."
            )

        # Traceability: who submitted + when (+ the not-invoice override reason, if any)
        invoice.submitted_by = submitted_by
        invoice.submitted_at = datetime.now(UTC)
        if override_reason and override_reason.strip():
            invoice.submit_override_reason = override_reason.strip()

        # --- Phase 2: approval rules ---
        # Hard overrides — always require human even if rule says auto_approve
        if invoice.new_vendor:
            invoice.status = InvoiceStatus.PENDING_APPROVAL.value
            db.commit()
            db.refresh(invoice)
            return {
                "status": InvoiceStatus.PENDING_APPROVAL.value,
                "message": "Invoice marked for approval (new vendor)",
                "invoice": _invoice_dict(invoice, db),
            }
        if invoice.coa_source in ("ai", None):
            invoice.status = InvoiceStatus.PENDING_APPROVAL.value
            db.commit()
            db.refresh(invoice)
            return {
                "status": InvoiceStatus.PENDING_APPROVAL.value,
                "message": "Invoice marked for approval (AI/unset COA requires verification)",
                "invoice": _invoice_dict(invoice, db),
            }

        # POL (Gaurav 2026-07-31): NO auto-approve. Every submitted invoice lands in
        # pending_approval for human sign-off — approval rules are not evaluated here.
        new_status = InvoiceStatus.PENDING_APPROVAL.value
        invoice.status = new_status
        db.commit()
        db.refresh(invoice)
        updated = invoice
        message = "Invoice marked for approval"

        from src.models.schemas import InvoiceResponse
        return {
            "status": new_status,
            "message": message,
            "invoice": InvoiceResponse.model_validate(updated).model_dump(),
        }

    # ── private helpers ────────────────────────────────────────────────────────



    def _ai_contract_review(self, db: Session, invoice: FinanceInvoice) -> dict:
        """
        Ask Claude Haiku to assess whether this invoice looks legitimate
        vs the linked contract (if any).
        """
        try:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                logger.warning("ANTHROPIC_API_KEY not set — skipping AI contract review")
                return {"assessment": "pass", "message": "AI review skipped (no API key)", "concerns": []}

            import anthropic

            # Gather contract info
            contract_info = "No contract on file for this vendor."
            if invoice.contract_id:
                contract = db.get(FinanceContract, invoice.contract_id)
                if contract:
                    contract_info = (
                        f"Contract: '{contract.name}' | Type: {contract.contract_type} | "
                        f"Frequency: {contract.frequency} | Expected amount: {contract.amount} {contract.currency} | "
                        f"Tolerance: ±{contract.tolerance_pct or 5}% | "
                        f"Active: {contract.is_active}"
                    )

            prompt = f"""You are a finance controller reviewing an invoice before it is approved.

Invoice details:
- Amount: {invoice.total_amount} {invoice.currency}
- Invoice date: {invoice.invoice_date}
- Invoice number: {invoice.invoice_number or 'not provided'}
- Expense account: {invoice.contra_account_code}
- Service period: {invoice.service_period_start} to {invoice.service_period_end or 'not specified'}
- Notes: {invoice.notes or 'none'}

{contract_info}

Assess: does this invoice look like a legitimate, expected charge?

Return ONLY a JSON object:
{{
  "assessment": "pass" or "flag" or "no_contract",
  "message": "1-2 sentence plain English explanation for the finance team",
  "concerns": ["specific concern 1", "specific concern 2"]
}}

Rules:
- "pass": amount matches contract within tolerance, everything looks normal
- "flag": amount differs significantly from contract, dates look wrong, or something seems unusual — explain clearly
- "no_contract": no contract exists for this vendor (use the contract_info above)
- concerns array should be empty if assessment is "pass" or "no_contract"
- Return ONLY the JSON"""

            client = anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = cast("TextBlock", message.content[0]).text.strip()
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            result = json.loads(response_text)
            return result

        except Exception as e:
            logger.error(f"AI contract review error: {e}", exc_info=True)
            # On error: pass with a warning (don't block the workflow)
            return {
                "assessment": "pass",
                "message": f"AI review could not be completed ({e}). Proceeding with manual review.",
                "concerns": [],
            }

    def _evaluate_approval_rules(
        self, db: Session, invoice: FinanceInvoice
    ) -> tuple[str, Optional[str]]:
        """
        Evaluate approval rules for this invoice.
        Returns (new_status, approved_by_label).
        """
        rules = (
            db.query(FinanceApprovalRule)
            .filter(
                FinanceApprovalRule.entity_id == invoice.entity_id,
                FinanceApprovalRule.status == "active",
            )
            .order_by(FinanceApprovalRule.priority.asc())
            .all()
        )

        amount = float(invoice.total_amount)

        for rule in rules:
            # Amount range check
            if rule.amount_min is not None and amount < float(rule.amount_min):
                continue
            if rule.amount_max is not None and amount > float(rule.amount_max):
                continue
            # COA prefix check
            if rule.coa_account_prefix and invoice.contra_account_code:
                if not invoice.contra_account_code.startswith(rule.coa_account_prefix):
                    continue
            elif rule.coa_account_prefix and not invoice.contra_account_code:
                continue
            # Vendor type check
            if rule.vendor_type and invoice.counterparty_id:
                from src.models.counterparty import FinanceCounterparty
                cp = db.get(FinanceCounterparty, invoice.counterparty_id)
                if cp and cp.type != rule.vendor_type:
                    continue

            # Rule matched
            if rule.action == "auto_approve":
                return InvoiceStatus.APPROVED.value, f"auto:rule_{rule.id}"
            else:
                return InvoiceStatus.PENDING_APPROVAL.value, None

        # No rule matched → require approval
        return InvoiceStatus.PENDING_APPROVAL.value, None


# Singleton instance
invoice_service = InvoiceService()
