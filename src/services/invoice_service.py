"""
Invoice Service

Business logic for managing invoices in the Accounts Payable workflow.
Handles creation, approval (with JE generation), rejection, voiding,
payment recording, AP knock-off lookups, and the AI contract review gate.
"""
import json
import logging
import os
from decimal import Decimal
from datetime import datetime, date, UTC
from typing import TYPE_CHECKING, Optional, cast

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.invoice import FinanceInvoice, InvoiceStatus
from src.models.contract import FinanceAmortizationSchedule, FinanceContract, FinanceApprovalRule
from src.models.counterparty import FinanceCounterparty
from src.models.schemas import InvoiceCreate, InvoiceUpdate
from src.services.journal_service import journal_service
from src.utils.errors import NotFoundError

# Statuses in which field edits (amount, COA, counterparty, dates, attach doc) are allowed — the
# unpaired, un-posted states (Gaurav, POL-107). Editing is BLOCKED the moment the invoice is
# PAIRED (a payment is attached — editing would desync the match; unpair back to reconcile first),
# APPROVED / partially_paid / paid (a journal entry is posted), or terminal (rejected / void).
# needs_fix + reconcile MUST be editable — needs_fix is literally the "fix the data" state, and
# reconcile is where the team corrects amount/counterparty before pairing.
EDITABLE_STATUSES = (
    InvoiceStatus.DRAFT.value,
    InvoiceStatus.RECONCILE.value,
    InvoiceStatus.NEEDS_FIX.value,
    InvoiceStatus.PENDING_APPROVAL.value,
)

if TYPE_CHECKING:
    from src.models.bank_account import FinanceBankAccount
    from src.models.journal_entry import FinanceJournalEntry
    from src.models.transaction import FinanceTransaction
    from anthropic.types import TextBlock

logger = logging.getLogger(__name__)

# Standard AP liability account
AP_ACCOUNT_CODE = "2000"
# Prepaid asset account for amortization (COA: 1300 Prepayments; 1200 is Trade Receivables).
# Single definition lives in amortization_service — the engine and the invoice must never
# disagree about where a prepayment parks.
from src.services.amortization_service import PREPAID_ACCOUNT_CODE  # noqa: E402
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

    # Pay Queue (POL-111): manual rank (NULL = FIFO by approved_at).
    result["pay_priority"] = invoice.pay_priority

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
            # escape LIKE wildcards so a literal % or _ in the term stays literal
            _esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{_esc}%"
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
        # HARD DUPLICATE BLOCK AT INGEST (Gaurav 2026-08-09, reverses the 2026-08-01 allow-at-draft
        # policy): a document seen before CANNOT be added. When a file hash is present (the upload
        # path), run the existing tiered detector; if it returns action="block" (L1 byte-identical
        # file, or L2 same vendor+invoice#+amount), REJECT — do not create the draft. Historical
        # bulk imports (no pdf_content_hash) are unaffected.
        if data.pdf_content_hash:
            from src.services.duplicate_detection_service import duplicate_detection_service
            from src.utils.errors import ConflictError
            verdict = duplicate_detection_service.detect(
                db, entity_id=data.entity_id, counterparty_id=data.counterparty_id,
                invoice_number=data.invoice_number, total_amount=data.total_amount,
                invoice_date=data.invoice_date, currency=data.currency,
                pdf_content_hash=data.pdf_content_hash,
            )
            if getattr(verdict, "action", None) == "block":
                raise ConflictError(
                    f"Duplicate invoice — {verdict.reason} This document cannot be added again."
                )
            # UPLOAD PATH = ZERO TOLERANCE (Gaurav 2026-08-17: "We should NOT allow upload of any
            # duplicate invoice. Period."). A REVIEW verdict (same vendor+number, different amount;
            # or no-number fuzzy match) is ALSO refused here — the uploader must void/supersede the
            # existing row first. Bulk ingests (no pdf hash) keep the flag-only behaviour by design.
            if getattr(verdict, "action", None) == "review":
                raise ConflictError(
                    f"Possible duplicate — {verdict.reason} Upload refused: resolve invoice "
                    f"#{verdict.duplicate_of} first (void it, or correct this document)."
                )

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
        """Update an invoice. Editable only in the unpaired/un-posted states (EDITABLE_STATUSES):
        draft, reconcile, needs_fix, pending_approval. Blocked once paired, posted, or terminal."""
        invoice = self.get_by_id(db, invoice_id)

        if invoice.status not in EDITABLE_STATUSES:
            from src.utils.errors import ConflictError
            hint = ("unpair it (back to reconcile) before editing"
                    if invoice.status == InvoiceStatus.PAIRED.value
                    else "void the journal entry or raise a credit note before changing the amount")
            raise ConflictError(
                f"Cannot edit an invoice in '{invoice.status}' status — {hint}."
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
        if invoice.status not in EDITABLE_STATUSES:
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Cannot attach a document to an invoice in '{invoice.status}' status "
                f"— a journal entry is already posted."
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

    def _is_spreadable_account(self, db: Session, code: Optional[str]) -> bool:
        """DA-14: only a P&L cost can be spread over a service period.

        Prepaid means "this cost has not become an expense YET" — the release has to land
        somewhere in the P&L. An ASSET account is already capitalized (the asset register
        amortizes it by policy); a LIABILITY/EQUITY/REVENUE account is not a cost at all.
        Unknown codes are treated as non-spreadable: refusing to spread is the safe default.
        """
        if not code:
            return False
        row = db.execute(text(
            "SELECT account_type FROM finance_accounts WHERE code = :c ORDER BY entity_id NULLS "
            "FIRST LIMIT 1"), {"c": code}).first()
        return bool(row) and str(row[0]).upper().endswith(("EXPENSE", "COST_OF_SALES"))

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

    # Books epoch: no Drive lah entity has activity before this. An invoice_date missing,
    # sentinel (1900-01-01 style), or pre-epoch must NEVER advance state — not even to
    # pending_approval (Gaurav, 2026-08-16: 21 backlog invoices with sentinel dates were
    # batch-posted into the year 1900, polluting every as-of figure).
    INVOICE_DATE_EPOCH = date(2016, 1, 1)

    def _guard_invoice_date(self, invoice) -> None:
        from src.utils.errors import ConflictError
        d = invoice.invoice_date
        if d is None or d < self.INVOICE_DATE_EPOCH:
            raise ConflictError(
                f"Invoice {invoice.id}: invoice_date {d} is missing or a sentinel/pre-epoch date "
                f"(< {self.INVOICE_DATE_EPOCH}). Fix the date first — the invoice belongs in "
                f"needs_fix and cannot advance to any state.")

    def approve(self, db: Session, invoice_id: int, approved_by: str, contra_account_code: Optional[str] = None) -> FinanceInvoice:
        """
        Approve an invoice, creating the corresponding journal entry.

        Standard case: Dr contra_account / Cr 2000 (Accounts Payable)
        Amortization case: Dr 1200 (Prepaid) / Cr 2000, plus amortization schedule
        """
        invoice = self.get_by_id(db, invoice_id)
        self._guard_invoice_date(invoice)

        # POL (Gaurav 2026-07-31): NO direct-to-approved. Every invoice must pass
        # through pending_approval first — a draft cannot be approved directly.
        if invoice.status != InvoiceStatus.PENDING_APPROVAL.value:
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Cannot approve invoice in '{invoice.status}' status. "
                f"Only pending_approval invoices can be approved (submit it first)."
            )

        # POL-106 (ground rule): a DUPLICATE can NEVER be approved — re-assert the block at
        # approval time, catching any duplicate that surfaced after submit. Clear it first.
        from src.services.duplicate_detection_service import duplicate_detection_service
        _dup = duplicate_detection_service.detect(
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
        if _dup.is_duplicate and _dup.duplicate_of and _dup.duplicate_of < invoice.id:
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Duplicate of invoice #{_dup.duplicate_of} ({_dup.level}) — a duplicate cannot "
                f"be approved (POL-106). Resolve the duplicate before approval."
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
        if total <= 0:
            from src.utils.errors import BadRequestError
            raise BadRequestError(
                f"Invoice {invoice_id} has a non-positive total ({total}) — cannot approve; "
                "fix the extraction first.")
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

        # DA-14 (Gaurav 2026-08-17): the ACCOUNT decides the route, and a service period is
        # only meaningful on the expense side of that fork. A cost is either waiting to become
        # an expense (prepaid: park in 1300 Prepayments, release monthly) or it is already an
        # asset (capitalized: park in the asset account, amortize by policy through the asset
        # register). It can never be both — spreading INTO an asset just shuffles money between
        # two assets and never reaches the P&L, while the register separately tries to age it.
        # Four live schedules did exactly that (3 -> 1710 Technology Development, 1 -> 2410
        # Convertible Notes). So: if the chosen account is not EXPENSE/COST_OF_SALES, the
        # service period is IGNORED — book straight to the chosen account, no schedule.
        _spreadable = self._is_spreadable_account(db, invoice.contra_account_code)
        needs_amortization = (
            invoice.service_period_start
            and invoice.service_period_end
            and _months_between(invoice.service_period_start, invoice.service_period_end) > 1
            and _spreadable
        )
        if (invoice.service_period_start and invoice.service_period_end and not _spreadable
                and _months_between(invoice.service_period_start, invoice.service_period_end) > 1):
            logger.warning(
                f"Invoice {invoice.id}: service period ignored — {invoice.contra_account_code} "
                f"is not an expense account, so this is capitalized spend (amortized by policy "
                f"through the asset register), not a prepayment.")

        if needs_amortization:
            debit_code = PREPAID_ACCOUNT_CODE
        else:
            debit_code = invoice.contra_account_code

        # Credit leg: dedicated liability if the chosen expense account declares one
        # (e.g. 6002 super -> 2302 payable), else generic 2000 AP. Resolve from the
        # real expense (invoice.contra_account_code), not the prepaid substitute.
        credit_code = self._payable_account_for(db, invoice.contra_account_code)

        inv_ref = f"Invoice {invoice.invoice_number or invoice.id}"

        # GST is CASH-BASIS (POL-118/119, Gaurav 2026-08-14): the bill books GROSS at
        # approval and posts NO 1350 GST Input line. The input GST credit is claimed
        # later, at the CASH payment, by the cash-time GST engine (which carves it out of
        # the expense). The GST amount stays recorded on the invoice (invoice.tax_amount)
        # for that claim; `tax`/`net` above are retained for validation/record only.
        # 2-line JE: Dr expense/prepaid (gross) / Cr payable (dedicated liability or 2000 AP).
        # POL-25 (fixed 2026-08-15, Gaurav audit): the ledger books FUNCTIONAL currency — a
        # foreign-currency invoice converts at the invoice-date monthly rate, with the native
        # amount + rate stamped on every line. Previously the raw invoice number booked unconverted.
        from src.services.fx_service import fx_service
        _func_ccy = _entity.base_currency if _entity else None
        _inv_ccy = invoice.currency or _func_ccy
        _total_native = Decimal(str(total))
        if _func_ccy and _inv_ccy != _func_ccy:
            _total_func, _fx_rate = fx_service.to_functional(
                db, _total_native, _inv_ccy, _func_ccy, invoice.invoice_date)
        else:
            _total_func, _fx_rate = _total_native, Decimal("1")
        lines = [
            {
                "account_code": debit_code,
                "debit_amount": float(_total_func),
                "credit_amount": 0.0,
                "description": inv_ref,
                "currency": _inv_ccy, "native_amount": _total_native, "fx_rate": _fx_rate,
            },
            {
                "account_code": credit_code,
                "debit_amount": 0.0,
                "credit_amount": float(_total_func),
                "description": inv_ref,
                "currency": _inv_ccy, "native_amount": _total_native, "fx_rate": _fx_rate,
            },
        ]

        # Gaurav ruling 2026-08-15: "at approval, the invoice posts, PERIOD" — no draft stage.
        from src.models.journal_entry import JournalEntryStatus
        entry = journal_service.create(
            db=db,
            entity_id=invoice.entity_id,
            entry_date=invoice.invoice_date,
            description=f"AP Invoice: {invoice.invoice_number or f'#{invoice.id}'}",
            lines=lines,
            status=JournalEntryStatus.POSTED,
            # the invoice is the ONE route allowed to park in Prepayments: it carries the
            # service period, and the schedule is created below in the same transaction (DA-15)
            prepaid_ok=needs_amortization,
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
            # POL-25 (2026-08-15): the schedule stores FUNCTIONAL amounts — a foreign-currency
            # invoice's schedule uses the converted total, matching the posted approval JE.
            _sched_total = float(_total_func)
            monthly_amount = round(_sched_total / months, 2)
            schedule = FinanceAmortizationSchedule(
                invoice_id=invoice.id,
                total_amount=_sched_total,
                months=months,
                monthly_amount=monthly_amount,
                expense_account_code=invoice.contra_account_code,
                prepaid_account_code=PREPAID_ACCOUNT_CODE,
                start_month=invoice.service_period_start.replace(day=1),
            )
            db.add(schedule)
            invoice.has_amortization_schedule = True
        else:
            # DA-15: capitalized spend bought on an invoice must enter the asset register HERE.
            # It has no bank transaction, so the old sweep refused it and it never depreciated
            # (11 journals, S$35,100.03 of Technology Development found stranded on the clone).
            from src.services.amortization_service import amortization_service as _amort
            _asset = _amort.register_from_journal(db, entry)
            if _asset is not None:
                logger.info(f"Invoice {invoice.id} capitalized -> asset schedule {_asset.id}")

        # Close the approval task (POL-108) — done whether approved directly or via the task.
        from src.services.task_service import task_service
        from src.models.task import TaskStatus
        task_service.close_for_source(db, f"invoice:{invoice.id}", TaskStatus.DONE.value,
                                      acted_by=approved_by, action="approve")

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
        """Reject an invoice with a reason. Captures who/when (rejected_by = logged-in user).

        Rejectable from draft / pending_approval AND from the pay queue (`approved`, POL-111).
        Rejecting an APPROVED invoice is not a status flip — approval already posted the bill JE
        (Dr expense / Cr 2000 AP), so we REVERSE that entry (void it) to clear the liability.
        A `paired`/`partially_paid`/`paid` invoice cannot be rejected — unpair / handle the
        payment first."""
        invoice = self.get_by_id(db, invoice_id)

        rejectable = (
            InvoiceStatus.DRAFT.value,
            InvoiceStatus.PENDING_APPROVAL.value,
            InvoiceStatus.APPROVED.value,
        )
        if invoice.status not in rejectable:
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Cannot reject invoice in '{invoice.status}' status "
                f"(only draft, pending_approval, or approved)."
            )

        # Approved → reverse the bill JE that approval posted, so no dangling liability remains.
        if invoice.status == InvoiceStatus.APPROVED.value and invoice.journal_entry_id:
            journal_service.void_entry(
                db, invoice.journal_entry_id,
                reason=f"invoice #{invoice.id} rejected from pay queue: {rejection_reason}",
            )
            invoice.journal_entry_id = None

        invoice.status = InvoiceStatus.REJECTED.value
        invoice.rejection_reason = rejection_reason
        invoice.rejected_by = rejected_by
        invoice.rejected_at = datetime.now(UTC)
        # Close the approval task (POL-108) — keeps the inbox in sync on a direct reject.
        from src.services.task_service import task_service
        from src.models.task import TaskStatus
        task_service.close_for_source(db, f"invoice:{invoice_id}", TaskStatus.RETURNED.value,
                                      acted_by=rejected_by, action="reject", notes=rejection_reason)
        db.commit()
        db.refresh(invoice)
        return invoice

    def mark_paid_already(self, db: Session, invoice_id: int, *, amount: Optional[float] = None,
                          source_bank_account_id: Optional[int] = None, reference: Optional[str] = None,
                          actor: Optional[str] = None) -> FinanceInvoice:
        """POL-135: 'paid outside the system'. Captures WHICH of our bank accounts it was paid from, the
        EXACT amount (can be a PARTIAL, ≤ remaining), and an OPTIONAL reference (no txn id — the operator
        won't have one). Moves the payable into the reconciliation arm (`reconcile`), where the
        categorization engine pairs the real bank payment (aided by the captured amount + account) and
        posts the knock-off → `paid`/`partially_paid`. Does NOT set amount_paid (paid <=> a matched txn).
        Every field is written to the append-only audit (POL-125). Displays as "Paid (reconciling)"."""
        from src.utils.errors import ConflictError, BadRequestError
        invoice = self.get_by_id(db, invoice_id)
        if invoice.status not in (InvoiceStatus.APPROVED.value, InvoiceStatus.PARTIALLY_PAID.value):
            raise ConflictError(
                f"Only an approved / partially-paid payable can be marked paid-already (is '{invoice.status}').")
        remaining = float(invoice.total_amount) - float(invoice.amount_paid or 0)
        amt = float(amount) if amount is not None else remaining
        if amt <= 0 or amt - remaining > 0.01:
            raise BadRequestError(f"Amount {amt} must be > 0 and ≤ the remaining balance {remaining:.2f}.")

        prior_status = invoice.status
        invoice.status = InvoiceStatus.RECONCILE.value
        # Full audit of the capture (source account / exact amount / optional reference). This is part of
        # the deliverable — the operator asked for a complete audit trail — so a failed audit ABORTS the
        # mark-paid rather than silently succeeding without a record.
        from src.models.payout_channels import FinancePayoutReferenceAudit
        db.add(FinancePayoutReferenceAudit(
            target_type="invoice_mark_paid", target_id=invoice_id, action="mark_paid",
            before={"status": prior_status, "amount_paid": float(invoice.amount_paid or 0)},
            after={"amount": amt, "source_bank_account_id": source_bank_account_id,
                   "reference": reference, "remaining_before": round(remaining, 2)},
            actor=actor, reason="paid outside the system"))
        # PM-7: record the manual payment in the SAME payout register (method='external_manual'), so every
        # payout — Wise or outside — is one register. No wise_transfer_id (paired via the reconcile lane,
        # not pair_on_import); state=awaiting_import (money already left us, awaiting the bank line to pair).
        from src.models.vendor_payout import FinanceVendorPayout, PayoutState
        db.add(FinanceVendorPayout(
            invoice_id=invoice_id, payable_type="invoice", payable_id=invoice_id,
            counterparty_id=invoice.counterparty_id, entity_id=invoice.entity_id,
            # our SOURCE account (source_bank_account_id) lives in the audit; the payout register routes
            # payees via registration_id (NULL here — a manual payment has no system-resolved recipient).
            amount=amt, currency=invoice.currency,
            method="external_manual", external_reference=reference,
            # RECONCILE (not AWAITING_IMPORT): a paid-outside payout has no wise_transfer_id, so Rung 1
            # can't pair it. RECONCILE is what the amount-fallback phase (3.7) settles against — mirrors
            # the claim/payroll reconcile lane. AWAITING_IMPORT left it unmatchable forever.
            state=PayoutState.RECONCILE.value, is_dry_run=False,
            requested_by=actor, requested_at=datetime.now(UTC), settled_at=datetime.now(UTC)))
        from src.services.task_service import task_service
        from src.models.task import TaskStatus
        task_service.close_for_source(db, f"invoice:{invoice_id}", TaskStatus.RETURNED.value,
                                      acted_by=actor, action="approve",
                                      notes=f"marked paid outside: {amt} from acct {source_bank_account_id}")
        db.commit(); db.refresh(invoice)
        return invoice

    def void(self, db: Session, invoice_id: int, voided_by: Optional[str] = None,
             void_reason: Optional[str] = None) -> FinanceInvoice:
        """Void an invoice. Allowed in any pre-posting state where nothing has hit the ledger:
        draft, needs_fix, reconcile, pending_approval, rejected. Blocked once money or the ledger
        is involved (paired/approved/paid/partially_paid). Captures who/when/why (voided_by)."""
        invoice = self.get_by_id(db, invoice_id)

        # needs_fix is a held exception (no money moved); reconcile is believed-paid-outside but NOT
        # yet paired/posted, so voiding it has no ledger effect either — both voidable like draft
        # (Gaurav 2026-08-09 needs_fix id 236; 2026-08-10 reconcile). paired stays blocked: posting authorized.
        allowed = (
            InvoiceStatus.DRAFT.value,
            InvoiceStatus.NEEDS_FIX.value,
            InvoiceStatus.RECONCILE.value,
            InvoiceStatus.PENDING_APPROVAL.value,
            InvoiceStatus.REJECTED.value,
        )
        if invoice.status not in allowed:
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Cannot void invoice in '{invoice.status}' status. "
                f"Only draft, needs_fix, reconcile, pending_approval, or rejected invoices can be voided."
            )

        invoice.status = InvoiceStatus.VOID.value
        invoice.voided_by = voided_by
        invoice.voided_at = datetime.now(UTC)
        invoice.void_reason = void_reason
        # Close the approval task (POL-108) on a direct void.
        from src.services.task_service import task_service
        from src.models.task import TaskStatus
        task_service.close_for_source(db, f"invoice:{invoice_id}", TaskStatus.CANCELLED.value,
                                      acted_by=voided_by, action="void", notes=void_reason)
        db.commit()
        db.refresh(invoice)
        return invoice

    # ── Pay Queue (POL-111) ────────────────────────────────────────────────────
    def pay_queue(self, db: Session, entity_id: Optional[int] = None) -> list[FinanceInvoice]:
        """The approved-payables queue: invoices awaiting payout. Sorted by manual priority
        first (a drag-reorder ranks the visible set 1..N; lower = higher), then FIFO by
        approval time for anything not manually ranked (Gaurav, POL-111)."""
        from sqlalchemy import asc
        q = db.query(FinanceInvoice).filter(
            FinanceInvoice.status.in_(
                (InvoiceStatus.APPROVED.value, InvoiceStatus.PARTIALLY_PAID.value)
            )
        )
        if entity_id is not None:
            q = q.filter(FinanceInvoice.entity_id == entity_id)
        # NULLS LAST so manually-ranked items sit on top in their chosen order, then the rest
        # by approval time (oldest first). Portable NULLS-LAST via a computed flag.
        return (
            q.order_by(
                asc(FinanceInvoice.pay_priority.is_(None)),
                asc(FinanceInvoice.pay_priority),
                asc(FinanceInvoice.approved_at),
                asc(FinanceInvoice.id),
            ).all()
        )

    def reorder_pay_queue(
        self, db: Session, ordered_invoice_ids: list[int], moved_by: Optional[str] = None
    ) -> list[FinanceInvoice]:
        """Apply a manual drag-reorder: write pay_priority = 1..N in the given order, and log
        every invoice whose position actually changed to the append-only move-log (POL-111).
        Only pay-queue invoices (approved / partially_paid) may be ranked."""
        from src.models.pay_queue_move import FinancePayQueueMove
        # Current order (before) to compute from→to positions for the trail.
        before = {inv.id: idx + 1 for idx, inv in enumerate(self.pay_queue(db))}
        pos = 0
        touched: list[FinanceInvoice] = []
        for iid in ordered_invoice_ids:
            inv = db.get(FinanceInvoice, int(iid))
            if inv is None or inv.status not in (
                InvoiceStatus.APPROVED.value, InvoiceStatus.PARTIALLY_PAID.value
            ):
                continue  # ignore anything not in the pay queue
            pos += 1
            new_from = before.get(inv.id)
            if inv.pay_priority != pos:
                if new_from != pos:
                    db.add(FinancePayQueueMove(
                        invoice_id=inv.id, from_position=new_from, to_position=pos,
                        moved_by=moved_by,
                    ))
                inv.pay_priority = pos
            touched.append(inv)
        db.commit()
        return self.pay_queue(db)

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

    def assert_not_duplicate(self, db: Session, invoice: "FinanceInvoice", action: str) -> None:
        """POL-106 hard gate, extended to the RECONCILE ARM (Gaurav 2026-08-17: invoice #1312 was
        PAID via pairing while flagged is_duplicate of #1291 — the arm never consulted the flag).
        Checks BOTH the stored ingest flag and a live detect(); a duplicate can never pair or post.
        First-one-wins: only blocks when the matched original has a LOWER id and is alive."""
        from src.services.duplicate_detection_service import duplicate_detection_service
        from src.utils.errors import ConflictError
        raw = invoice.ai_extraction_raw or {}
        stored = ((raw.get("recon") or {}).get("duplicate") or {}) if isinstance(raw, dict) else {}
        if stored.get("is_duplicate"):
            raise ConflictError(
                f"Invoice {invoice.id} is flagged as a DUPLICATE ({stored.get('duplicate_of') or 'see recon'}) "
                f"— it cannot {action}. Void it (first one wins) or clear the flag with an explicit override.")
        v = duplicate_detection_service.detect(
            db, entity_id=invoice.entity_id, counterparty_id=invoice.counterparty_id,
            invoice_number=invoice.invoice_number, total_amount=invoice.total_amount,
            invoice_date=invoice.invoice_date, currency=invoice.currency,
            pdf_content_hash=invoice.pdf_content_hash)
        if getattr(v, "action", None) == "block" and (v.duplicate_of or 0) < invoice.id:
            raise ConflictError(
                f"Invoice {invoice.id} duplicates invoice #{v.duplicate_of} ({v.reason}) — it cannot "
                f"{action}. Void it (first one wins).")

    def post_pairing(self, db: Session, invoice_id: int, posted_by: str = "ui") -> dict:
        """Post a PAIRED (reconcile-arm) invoice — the Mechanism-A posting action (Gaurav, 2026-08-15).

        Algorithm (locked in STATUS I-5 v1):
          1. invoice must be `paired`; every matched txn must be books-open (>= 2026-01-01) — pre-2026
             pairings REFUSE (their cash lives in opening balances; the history pipeline books them).
          2. VOID the txn's interim JE if one exists (categorized/parked; its GST lines unwind with it).
          3. Bill JE: entity-functional at the invoice-date rate, GROSS (POL-121), POSTED.
          4. Payment via create_ap_payment_entries — the full stack: payment-date currency conversion,
             the POL-123 GST claim, auto-FX residue clearing. Posted, txn RECONCILED, match logged.
        """
        from datetime import date as _date, datetime as _dt, UTC as _UTC
        from src.models.transaction import FinanceTransaction, TransactionStatus
        from src.models.bank_account import FinanceBankAccount
        from src.models.invoice_payment_match import FinanceInvoicePaymentMatch
        from src.models.entity import FinanceEntity
        from src.models.journal_entry import JournalEntryStatus
        from src.services.fx_service import fx_service
        from src.utils.errors import BadRequestError

        # Row-lock the invoice so two concurrent post-pairing requests (double-click) serialize:
        # the second sees the post-transition status and refuses cleanly.
        invoice = (db.query(FinanceInvoice)
                   .filter(FinanceInvoice.id == invoice_id)
                   .with_for_update().first())
        if invoice is None:
            raise NotFoundError(f"Invoice {invoice_id} not found.")
        self.assert_not_duplicate(db, invoice, "post via pairing")
        if invoice.status != InvoiceStatus.PAIRED.value:
            raise BadRequestError(f"Invoice {invoice_id} is not in paired status (is: {invoice.status}).")
        if invoice.journal_entry_id is not None:
            raise BadRequestError(
                f"Invoice {invoice_id} already carries bill JE {invoice.journal_entry_id} — "
                "refusing to double-post.")
        matches = (db.query(FinanceInvoicePaymentMatch)
                   .filter(FinanceInvoicePaymentMatch.invoice_id == invoice_id).all())
        if not matches:
            raise BadRequestError(f"Invoice {invoice_id} has no payment matches.")
        txns = [db.get(FinanceTransaction, m.transaction_id) for m in matches]
        if any(t is None for t in txns):
            raise BadRequestError("A matched transaction no longer exists.")
        for t in txns:
            if t.transaction_date < _date(2026, 1, 1):
                raise BadRequestError(
                    f"Txn {t.id} is dated {t.transaction_date} (pre-books-open). Pre-2026 pairings are "
                    "posted by the history pipeline, never here (POL-124/POL-28).")

        # 2) void any interim JEs on the matched txns
        voided = []
        for t in txns:
            if t.reconciled_journal_entry_id:
                je = journal_service.void_entry(
                    db, t.reconciled_journal_entry_id,
                    reason=f"re-routed through AP by post_pairing of invoice {invoice_id}")
                if je is not None:
                    voided.append(je.id)
                t.reconciled_journal_entry_id = None

        # 3) bill JE — functional at invoice-date rate, gross, POSTED
        entity_row = db.get(FinanceEntity, invoice.entity_id)
        func_ccy = entity_row.base_currency if entity_row else None
        inv_ccy = invoice.currency or func_ccy
        total_native = Decimal(str(invoice.total_amount))
        if func_ccy and inv_ccy != func_ccy:
            total_func, fx_rate = fx_service.to_functional(
                db, total_native, inv_ccy, func_ccy, invoice.invoice_date)
        else:
            total_func, fx_rate = total_native, Decimal("1")
        debit_code = invoice.contra_account_code
        if not debit_code:
            raise BadRequestError(f"Invoice {invoice_id} has no expense account (contra_account_code).")
        credit_code = self._payable_account_for(db, debit_code)
        inv_ref = f"Invoice {invoice.invoice_number or invoice.id}"
        meta = {"currency": inv_ccy, "native_amount": total_native, "fx_rate": fx_rate}
        bill = journal_service.create(
            db=db, entity_id=invoice.entity_id, entry_date=invoice.invoice_date,
            description=f"AP Invoice (post-pairing): {invoice.invoice_number or f'#{invoice.id}'}",
            lines=[
                {"account_code": debit_code, "debit_amount": float(total_func), "credit_amount": 0.0,
                 "description": inv_ref, **meta},
                {"account_code": credit_code, "debit_amount": 0.0, "credit_amount": float(total_func),
                 "description": inv_ref, **meta},
            ],
            status=JournalEntryStatus.POSTED,
        )
        bill.source = "pairing_post"
        bill.reference_number = f"INV-{invoice.id}"
        db.flush()
        invoice.journal_entry_id = bill.id

        # 4) payment(s) via the full-stack constructor
        payment_ids = []
        for t in txns:
            bank_account = db.get(FinanceBankAccount, t.bank_account_id)
            if not bank_account or not bank_account.coa_account_code:
                raise BadRequestError(f"Bank account for txn {t.id} has no COA code.")
            entry = self.create_ap_payment_entries(
                db=db, bank_account=bank_account, invoice=invoice,
                txn_date=t.transaction_date, abs_amount=abs(float(t.amount)),
                source="pairing_post",
                description=f"AP Payment (post-pairing by {posted_by}): {inv_ref}")
            entry.status = "POSTED"
            entry.posted_at = _dt.now(_UTC)
            t.reconciled_journal_entry_id = entry.id
            t.status = TransactionStatus.RECONCILED
            payment_ids.append(entry.id)
            # amount_paid lives in the INVOICE's currency (record_payment convention). A payment
            # from a bank in another currency converts at the payment date; when it settles the
            # invoice (within the 2-cent tolerance), record the FULL invoice total so the header
            # closes exactly (Gaurav ruling, 2026-08-15).
            _pay_dec = Decimal(str(abs(float(t.amount))))
            _txn_ccy = (t.currency or "").upper()
            _icy = (invoice.currency or "").upper()
            if _txn_ccy and _icy and _txn_ccy != _icy:
                _pay_dec, _ = fx_service.to_functional(db, _pay_dec, _txn_ccy, _icy, t.transaction_date)
            _remaining = Decimal(str(invoice.total_amount)) - Decimal(str(invoice.amount_paid))
            if abs(_pay_dec - _remaining) <= Decimal("0.02") or _pay_dec > _remaining:
                _pay_dec = _remaining
            # Apply in-transaction (NOT record_payment — it commits internally, which would strand
            # a half-posted invoice if a later payment leg raises; the whole action commits once).
            _new_paid = round(float(invoice.amount_paid) + float(_pay_dec), 2)
            invoice.amount_paid = _new_paid
            invoice.status = (InvoiceStatus.PAID.value if _new_paid >= float(invoice.total_amount)
                              else InvoiceStatus.PARTIALLY_PAID.value)
        for m in matches:
            m.state = "logged"
        db.commit()
        db.refresh(invoice)
        return {"invoice_id": invoice.id, "status": invoice.status, "bill_je": bill.id,
                "payment_jes": payment_ids, "voided_interim_jes": voided}

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

        # RECONCILE included: a paid-outside invoice (mark_paid_already) settles here when the bank line
        # arrives, via the Phase 3.7 register knock-off. Without it, paid-outside invoices could never pair.
        open_statuses = (InvoiceStatus.APPROVED.value, InvoiceStatus.PARTIALLY_PAID.value,
                         InvoiceStatus.RECONCILE.value)
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

    def _input_gst_reclass(self, db: Session, invoice: FinanceInvoice, paid_amount: float):
        """PR-1: at cash payment of a GROSS-booked bill (POL-121), return (expense_code, gst_amount)
        for the paid slice's claimable input GST — Dr 1350 / Cr <expense COA>. classify() is the ONE
        gate (respects the invoice's own tax, the account flag, and the vendor gate / DQ-99). Partial
        payments scale the full-invoice GST by the paid fraction. (None, 0.0) means no claim."""
        from src.services import gst_service
        expense_code = invoice.contra_account_code
        total = float(invoice.total_amount or 0)
        if not expense_code or total <= 0:
            return None, 0.0
        market = gst_service.market_for_entity(invoice.entity_id)
        vendor_flag = (gst_service.vendor_registered(db, invoice.counterparty_id, market)
                       if invoice.counterparty_id else None)
        verdict = gst_service.classify(
            entity_registered=gst_service.entity_is_gst_registered(db, invoice.entity_id),
            account_applicable=gst_service.account_gst_applicable(db, expense_code, market),
            direction="input", leg_touches_bank=True, gross=total,
            has_invoice=True, invoice_tax=invoice.tax_amount,
            vendor_registered_flag=vendor_flag,
        )
        if verdict.get("account") != gst_service.GST_INPUT or verdict.get("amount", 0.0) <= 0:
            return None, 0.0
        fraction = min(1.0, float(paid_amount) / total)
        return expense_code, round(float(verdict["amount"]) * fraction, 2)

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
        from src.services import gst_service

        bank_entity_id = bank_account.entity_id
        invoice_entity_id = invoice.entity_id
        bank_coa = bank_account.coa_account_code
        inv_ref = f"Invoice {invoice.invoice_number or invoice.id}"

        # POL-25 (fixed 2026-08-15, Gaurav audit): abs_amount arrives in the BANK ACCOUNT's native
        # currency; the ledger books functional. Convert at the payment date and stamp native facts.
        from src.models.entity import FinanceEntity
        from src.services.fx_service import fx_service
        _bank_entity = db.get(FinanceEntity, bank_entity_id)
        _func_ccy = _bank_entity.base_currency if _bank_entity else None
        _bank_ccy = bank_account.currency or _func_ccy
        _native_amt = Decimal(str(abs_amount))
        if _func_ccy and _bank_ccy != _func_ccy:
            _func_amt, _fx_rate = fx_service.to_functional(db, _native_amt, _bank_ccy, _func_ccy, txn_date)
            abs_amount = float(_func_amt)
        else:
            _fx_rate = Decimal("1")
        _ccy_meta = {"currency": _bank_ccy, "native_amount": _native_amt, "fx_rate": _fx_rate}

        # Clear the SAME liability the approval JE credited — dedicated (e.g. 2302
        # Superannuation Payable) or generic 2000 AP — else the sub-payable never closes.
        ap_code = self._payable_account_for(db, invoice.contra_account_code)

        if bank_entity_id == invoice_entity_id:
            # ── Same-entity: Dr AP / Cr Bank, plus (PR-1) the cash-time input-GST claim ─────
            lines = [
                {"account_code": ap_code, "debit_amount": abs_amount, "credit_amount": 0.0,
                 "description": inv_ref, **_ccy_meta},
                {"account_code": bank_coa, "debit_amount": 0.0, "credit_amount": abs_amount,
                 "description": inv_ref, **_ccy_meta},
            ]
            # POL-121: the bill was booked GROSS at approval (no 1350). Claim the paid slice's input
            # GST NOW, at cash payment: Dr 1350 / Cr <expense COA>, moving GST out of the gross expense
            # into the receivable, dated in the payment's BAS quarter. classify() is the gate.
            exp_code, gst_amt = self._input_gst_reclass(db, invoice, abs_amount)
            if exp_code and gst_amt > 0:
                lines.append({"account_code": gst_service.GST_INPUT, "debit_amount": gst_amt,
                              "credit_amount": 0.0, "description": f"{inv_ref} — input GST claimed at payment"})
                lines.append({"account_code": exp_code, "debit_amount": 0.0, "credit_amount": gst_amt,
                              "description": f"{inv_ref} — expense net of GST (POL-121)"})

            # AUTO-FX CLEARING (Gaurav, 2026-08-15): when this payment settles the invoice in NATIVE
            # terms, any functional-currency residue on the payable (invoice-date rate vs payment-date
            # actual) clears to 7100 FX Gains/Losses ON THE SAME JE — a foreign-currency payment can
            # never strand a residue on AP. Traced via the shared INV-<id> reference.
            if _bank_ccy != _func_ccy or (invoice.currency and invoice.currency != _func_ccy):
                from sqlalchemy import text as _text
                _ap_open = Decimal(str(db.execute(_text(
                    """SELECT coalesce(sum(l.credit_amount - l.debit_amount), 0)
                       FROM finance_journal_lines l JOIN finance_journal_entries je ON je.id = l.entry_id
                       WHERE je.status = 'POSTED' AND l.account_code = :ap AND l.entity_id = :ent
                         AND je.reference_number = :ref"""),
                    {"ap": ap_code, "ent": bank_entity_id, "ref": f"INV-{invoice.id}"}).scalar() or 0))
                _residue = _ap_open - Decimal(str(abs_amount))  # what this payment leaves behind
                # Settlement is judged in the INVOICE's currency: a payment from a bank in a third
                # currency converts at the payment date before comparing against the invoice total.
                _inv_ccy = (invoice.currency or _func_ccy).upper()
                if _bank_ccy == _inv_ccy:
                    _paid_native = _native_amt
                else:
                    _paid_native, _ = fx_service.to_functional(db, _native_amt, _bank_ccy, _inv_ccy, txn_date)
                _inv_total = Decimal(str(invoice.total_amount or 0))
                _fully_paid = abs(_paid_native - _inv_total) <= Decimal("0.02") or _paid_native >= _inv_total
                if _fully_paid and abs(_residue) > Decimal("0.005"):
                    if _residue > 0:   # payable exceeds cash -> FX gain
                        lines.append({"account_code": ap_code, "debit_amount": float(_residue),
                                      "credit_amount": 0.0, "description": f"{inv_ref} — FX clearing"})
                        lines.append({"account_code": "7100", "debit_amount": 0.0,
                                      "credit_amount": float(_residue), "description": f"{inv_ref} — FX gain"})
                    else:
                        lines.append({"account_code": "7100", "debit_amount": float(-_residue),
                                      "credit_amount": 0.0, "description": f"{inv_ref} — FX loss"})
                        lines.append({"account_code": ap_code, "debit_amount": 0.0,
                                      "credit_amount": float(-_residue), "description": f"{inv_ref} — FX clearing"})
            entry = journal_service.create(
                db=db,
                entity_id=bank_entity_id,
                entry_date=txn_date,
                description=description,
                lines=lines,
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
                    **_ccy_meta,
                },
                {
                    "account_code": bank_coa,
                    "debit_amount": 0.0,
                    "credit_amount": abs_amount,
                    "description": inv_ref,
                    **_ccy_meta,
                },
            ],
        )
        bank_entry.source = source
        bank_entry.reference_number = f"INV-{invoice.id}"  # trace JE -> invoice
        bank_entry.intercompany_group_id = ic_group_id

        # Invoice entity: Dr AP (dedicated liability or 2000) / Cr IC Payable.
        # POL-25/27: each entity books in ITS OWN functional currency — the invoice entity may
        # differ from the bank entity (AU bank paying an SG invoice). Convert the bank-native
        # cash to the invoice entity's functional at the payment date; same native facts stamped.
        _inv_entity = db.get(FinanceEntity, invoice_entity_id)
        _inv_func_ccy = _inv_entity.base_currency if _inv_entity else None
        if _inv_func_ccy and _bank_ccy and _inv_func_ccy != _bank_ccy:
            _inv_leg_amt, _inv_fx_rate = fx_service.to_functional(
                db, _native_amt, _bank_ccy, _inv_func_ccy, txn_date)
            inv_amount = float(_inv_leg_amt)
        else:
            inv_amount, _inv_fx_rate = float(_native_amt), Decimal("1")
        _inv_ccy_meta = {"currency": _bank_ccy, "native_amount": _native_amt, "fx_rate": _inv_fx_rate}
        inv_entry = journal_service.create(
            db=db,
            entity_id=invoice_entity_id,
            entry_date=txn_date,
            description=description,
            lines=[
                {
                    "account_code": ap_code,
                    "debit_amount": inv_amount,
                    "credit_amount": 0.0,
                    "description": inv_ref,
                    **_inv_ccy_meta,
                },
                {
                    "account_code": ic_payable,
                    "debit_amount": 0.0,
                    "credit_amount": inv_amount,
                    "description": inv_ref,
                    **_inv_ccy_meta,
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
        self._guard_invoice_date(invoice)

        # Submittable from draft OR needs_fix (re-submit after resolving an exception).
        if invoice.status not in (InvoiceStatus.DRAFT.value, InvoiceStatus.NEEDS_FIX.value):
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Only draft or needs_fix invoices can be submitted. Current status: {invoice.status}"
            )

        # --- Exception screen (POL-107 state machine). A guardrail failure PARKS the
        #     invoice in needs_fix (a tracked worklist state) with the reasons recorded,
        #     rather than throwing a dead API error. The approval agent's job: nothing
        #     with an open exception reaches pending_approval. ---
        reasons: list[str] = []

        # 1. Field completeness
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
            reasons.append("Missing required fields: " + ", ".join(missing))

        # 2. Duplicate (POL-106 hard block). "First one wins": flag only if the matched
        #    original was ingested BEFORE this one (lower id).
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
        is_duplicate = bool(dup.is_duplicate and dup.duplicate_of and dup.duplicate_of < invoice.id)
        if is_duplicate:
            reasons.append(
                f"Duplicate of invoice #{dup.duplicate_of} ({dup.level}). {dup.reason}"
            )

        # 3. COA-required anchors (AW-2 door gate, request-route only). The COA config decides which
        #    supporting info this expense demands; captured on finance_invoice_metadata at ratify.
        if invoice.contra_account_code:
            from src.services import coa_config_service
            from src.models.invoice_approval import FinanceInvoiceMetadata
            req = coa_config_service.require_fields(db, invoice.contra_account_code)
            meta = db.query(FinanceInvoiceMetadata).filter(
                FinanceInvoiceMetadata.invoice_id == invoice.id
            ).first()
            need = []
            # The trip requirement is satisfied by a trip id OR a vehicle rego — a cost isn't always
            # trip-specific (e.g. towing is vehicle-level). Either operational anchor clears the gate.
            if req.get("trip_id") and not (meta and (meta.trip_id or meta.rego)):
                need.append("trip id or vehicle rego")
            if req.get("intercom_id") and not (meta and meta.intercom_ticket_id):
                need.append("ticket number")
            if need:
                reasons.append(f"COA {invoice.contra_account_code} requires: " + ", ".join(need))

        # 4. Vendor must be finance-approved (POL-115 — no auto-activation). This is NOT a team-fix
        #    exception (needs_fix) — it's WAITING ON FINANCE. So the invoice STAYS IN DRAFT; when
        #    finance approves the vendor, approve_vendor() auto-submits the draft. Return early so
        #    the invoice never advances past draft while its vendor is pending.
        if invoice.counterparty_id:
            cp = db.get(FinanceCounterparty, invoice.counterparty_id)
            _ctype = str(getattr(cp.type, "value", cp.type)).lower() if cp else ""
            _cstatus = str(getattr(cp.status, "value", cp.status)).lower() if cp else ""
            # Hold only genuinely-pending vendors = NOT active. New vendors are created 'inactive'
            # (counterparty_service.create), so status catches them; approve_vendor flips to active.
            # Do NOT also require is_verified — 82 legacy vendors are active-but-unverified (e.g.
            # Experian) and are legitimately usable; keying on is_verified wrongly held their invoices
            # in draft with no vendor task to clear them (Gaurav 2026-08-10).
            if cp and _ctype == "vendor" and _cstatus != "active":
                invoice.submitted_by = submitted_by
                invoice.submitted_at = datetime.now(UTC)
                db.commit()
                db.refresh(invoice)
                return {"status": InvoiceStatus.DRAFT.value,
                        "message": f"Held in draft — vendor '{cp.name}' is awaiting finance approval.",
                        "invoice": _invoice_dict(invoice, db)}

        if reasons:
            # Park in needs_fix; stamp the reasons + the duplicate flag (POL-106 gate reads it).
            raw = dict(invoice.ai_extraction_raw or {})
            raw["needs_fix"] = {
                "reasons": reasons,
                "is_duplicate": is_duplicate,
                "duplicate_of": dup.duplicate_of if is_duplicate else None,
                "flagged_at": datetime.now(UTC).isoformat(),
            }
            invoice.ai_extraction_raw = raw
            invoice.submitted_by = submitted_by
            invoice.submitted_at = datetime.now(UTC)
            invoice.status = InvoiceStatus.NEEDS_FIX.value
            db.commit()
            db.refresh(invoice)
            return {
                "status": InvoiceStatus.NEEDS_FIX.value,
                "message": "Moved to needs_fix — resolve before approval: " + "; ".join(reasons),
                "reasons": reasons,
                "is_duplicate": is_duplicate,
                "invoice": _invoice_dict(invoice, db),
            }

        # Passed the screen — clear any prior needs_fix stamp (re-submit after a fix).
        if (invoice.ai_extraction_raw or {}).get("needs_fix"):
            raw = dict(invoice.ai_extraction_raw or {})
            raw.pop("needs_fix", None)
            invoice.ai_extraction_raw = raw

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
        # POL (Gaurav 2026-07-31): NO auto-approve — every submitted invoice lands in
        # pending_approval for human sign-off. POL-108: entering pending_approval ALWAYS
        # creates an assigned approval task (the assignee gate) + attaches the AI review.
        if invoice.new_vendor:
            return self._enter_pending_approval(db, invoice, "Invoice marked for approval (new vendor)")
        if invoice.coa_source in ("ai", None):
            return self._enter_pending_approval(
                db, invoice, "Invoice marked for approval (AI/unset COA requires verification)")
        return self._enter_pending_approval(db, invoice, "Invoice marked for approval")

    # ── private helpers ────────────────────────────────────────────────────────



    # Default approver while approval authorities are not yet configured (POL-108):
    # zilla@ guards ALL approvals. Future: route by authority/threshold (some auto-approve).
    _DEFAULT_APPROVER_EMAIL = "zilla@drivelah.sg"
    _APPROVAL_HIGH_RISK_MIN = 1000  # amount at/above this flags the task 'high' risk

    def get_metadata(self, db: Session, invoice_id: int) -> dict:
        from src.models.invoice_approval import FinanceInvoiceMetadata
        m = db.query(FinanceInvoiceMetadata).filter(
            FinanceInvoiceMetadata.invoice_id == invoice_id).first()
        return m.to_dict() if m else {"invoice_id": invoice_id, "trip_id": None,
                                      "intercom_ticket_id": None, "rego": None, "claim_ref": None}

    def set_metadata(self, db: Session, invoice_id: int, fields: dict) -> dict:
        """Upsert the supporting anchors captured at ratification (trip / ticket / rego / claim)."""
        from src.models.invoice_approval import FinanceInvoiceMetadata
        from datetime import datetime, UTC
        m = db.query(FinanceInvoiceMetadata).filter(
            FinanceInvoiceMetadata.invoice_id == invoice_id).first()
        if m is None:
            m = FinanceInvoiceMetadata(invoice_id=invoice_id)
            db.add(m)
        for k in ("trip_id", "intercom_ticket_id", "rego", "claim_ref"):
            if k in fields:
                v = fields[k]
                setattr(m, k, (str(v).strip() or None) if v is not None else None)
        m.updated_at = datetime.now(UTC)
        db.flush()
        db.commit()
        return m.to_dict()

    def raise_invoice(self, db: Session, payload: dict) -> dict:
        """Flow 2 — the Raise-a-vendor-invoice front door.

        1) gate the COA's required anchors (finance_coa_config door gate — request-route only);
        2) create the draft; 3) store the captured anchors on finance_invoice_metadata;
        4) submit → pending_approval (assigns the task to the config approver, POL-115).
        Returns the invoice (with internal_ref) + who it's assigned to.
        """
        from src.models.schemas import InvoiceCreate
        from src.models.invoice_approval import FinanceInvoiceMetadata
        from src.services import coa_config_service
        from src.utils.errors import ConflictError

        coa = payload.get("contra_account_code")
        trip_id = (payload.get("trip_id") or "").strip() or None
        ticket = (payload.get("intercom_ticket_id") or "").strip() or None
        rego = (payload.get("rego") or "").strip() or None
        claim_ref = (payload.get("claim_ref") or "").strip() or None

        # DOOR gate — presence of the anchors this COA demands (live TMS/Intercom validity is AW-5).
        req = coa_config_service.require_fields(db, coa) if coa else {"trip_id": False, "intercom_id": False, "other": None}
        missing = []
        if req.get("trip_id") and not trip_id:
            missing.append("trip_id")
        if req.get("intercom_id") and not ticket:
            missing.append("intercom_ticket_id")
        if missing:
            raise ConflictError(f"COA {coa} requires: {', '.join(missing)}")

        # New vendor (Flow 3): create a PENDING counterparty (inactive + unverified) — never active —
        # so finance must approve it (POL-115). The invoice links to it and holds in draft.
        new_vendor = bool(payload.get("new_vendor"))
        counterparty_id = payload.get("counterparty_id")
        if new_vendor and not counterparty_id and payload.get("vendor_name"):
            cp = FinanceCounterparty(
                name=str(payload["vendor_name"]).strip(),
                type="vendor", status="inactive", is_verified=False,
                is_gst_registered=bool(payload.get("is_gst_registered", False)),
            )
            db.add(cp)
            db.flush()
            counterparty_id = cp.id

        create = InvoiceCreate(
            entity_id=payload["entity_id"],
            counterparty_id=counterparty_id,
            invoice_number=payload.get("invoice_number"),
            invoice_date=payload["invoice_date"],
            total_amount=payload["total_amount"],
            currency=payload["currency"],
            contra_account_code=coa,
            uploaded_by=payload.get("uploaded_by"),
            notes=payload.get("notes"),
            new_vendor=new_vendor,
        )
        invoice = self.create(db, create)

        if any([trip_id, ticket, rego, claim_ref]):
            db.add(FinanceInvoiceMetadata(
                invoice_id=invoice.id, trip_id=trip_id, intercom_ticket_id=ticket,
                rego=rego, claim_ref=claim_ref,
            ))
            db.flush()

        # New vendor not yet finance-approved → hold the invoice in DRAFT (POL-115) and raise a
        # finance vendor-approval TASK. When finance approves the vendor, the invoice auto-submits.
        if new_vendor and counterparty_id:
            from src.services.task_service import task_service
            task_service.enqueue(
                db, type="vendor-approval", source_ref=f"vendor:{counterparty_id}",
                title=f"Approve new vendor — {payload.get('vendor_name') or ('cp ' + str(counterparty_id))}",
                summary=f"New vendor requested on {_invoice_dict(invoice, db).get('internal_ref')}; "
                        f"fill details + approve to unblock the invoice.",
                assignee_role="finance.invoices", created_by=payload.get("uploaded_by"),
            )
            db.commit()
            return {"status": InvoiceStatus.DRAFT.value,
                    "message": "Vendor requested — invoice held in draft until finance approves the vendor.",
                    "invoice": _invoice_dict(invoice, db)}

        return self.submit(db, invoice.id, confirmed=True, submitted_by=payload.get("uploaded_by"))

    def approve_vendor(self, db: Session, counterparty_id: int, approved_by: str) -> dict:
        """Finance approves a requested vendor → activate + verify it, then auto-submit any invoices
        held in draft awaiting it (Flow 3)."""
        cp = db.get(FinanceCounterparty, counterparty_id)
        if cp is None:
            from src.utils.errors import NotFoundError
            raise NotFoundError(f"counterparty {counterparty_id} not found")
        cp.status = "active"
        cp.is_verified = True
        db.flush()
        # Any draft awaiting this vendor auto-submits now (not just new_vendor-flagged ones — a draft
        # created via the ratify quick-add isn't flagged but is equally held).
        held = db.query(FinanceInvoice).filter(
            FinanceInvoice.counterparty_id == counterparty_id,
            FinanceInvoice.status == InvoiceStatus.DRAFT.value,
        ).all()
        db.commit()
        submitted, failed = [], []
        for inv in held:
            try:
                self.submit(db, inv.id, confirmed=True, submitted_by=approved_by)
                submitted.append(inv.id)
            except Exception as exc:
                # PR-2: NEVER swallow. A held invoice that fails to auto-submit on vendor approval
                # is a stuck worklist item — log it, and raise a finance task so a human clears it.
                # (Safe w.r.t. prior iterations: every successful submit() path COMMITS internally,
                # so this rollback only reverts the failed submit's uncommitted work — re-review F1.)
                db.rollback()
                logger.exception("approve_vendor: auto-submit failed for invoice %s (vendor %s)",
                                 inv.id, counterparty_id)
                failed.append({"invoice_id": inv.id, "error": str(exc)})
                try:
                    from src.services.task_service import task_service
                    task_service.enqueue(
                        db, type="invoice-fix",
                        title=f"Auto-submit failed after vendor approval — invoice #{inv.id}",
                        source_ref=f"invoice:{inv.id}", source_system="finance",
                        summary=(f"Vendor {cp.name or counterparty_id} was approved but invoice #{inv.id} "
                                 f"could not auto-submit: {exc}"),
                        assignee_role="finance.invoices", risk="medium", created_by=approved_by,
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception("approve_vendor: could not enqueue follow-up task for invoice %s", inv.id)
        return {"counterparty_id": counterparty_id, "status": "active",
                "submitted_invoices": submitted, "failed_invoices": failed}

    def _enter_pending_approval(self, db: Session, invoice: FinanceInvoice, message: str) -> dict:
        """Move an invoice to pending_approval AND create its assigned approval task — atomically.
        This IS the assignee gate (POL-108): an invoice never sits in pending_approval without a
        task assigned to someone. Runs the AI contract review and attaches it as the card context.
        """
        from sqlalchemy import text
        from src.services.task_service import task_service
        from src.services.duplicate_detection_service import duplicate_detection_service
        from src.models.counterparty import FinanceCounterparty

        # Resolve the approver from the COA config (AW-2 sign-off gate): the invoice's expense COA
        # decides approver_1 (an onboarded employee). This config applies ONLY to the request-raise
        # route (raised invoices / payout requests) — automated economic-event postings never enter
        # this path. Fall back to the default approver, then a role queue, so the assignee gate
        # ALWAYS holds even if config/user can't be resolved.
        from src.services import coa_config_service
        route = coa_config_service.routing(
            db, invoice.contra_account_code,
            Decimal(str(invoice.total_amount)) if invoice.total_amount is not None else None,
        )
        approver_email = route.get("approver_1") or self._DEFAULT_APPROVER_EMAIL
        approver_id = db.execute(
            text("SELECT id FROM users WHERE lower(email) = :e"),
            {"e": str(approver_email).lower()},
        ).scalar()
        assignee_role = None if approver_id else "finance.invoices"

        # Approval Agent v2 card (POL-109) — ClickHouse-sourced enrichment + double-pay +
        # Sonnet summary/risk/confidence. BEST-EFFORT: on failure fall back to a minimal card so
        # the task is never blocked (the assignee gate still holds).
        from src.services import approval_card_service
        amount = float(invoice.total_amount) if invoice.total_amount is not None else 0.0
        body = approval_card_service.build_card_body(db, invoice)
        if not body:
            cp = db.get(FinanceCounterparty, invoice.counterparty_id) if invoice.counterparty_id else None
            vendor = (cp.name if cp else None) or "Unknown vendor"
            body = {"agent_version": "v2-min", "vendor": vendor, "summary": None,
                    "risk_flags": [], "confidence": None}
        vendor = body.get("vendor") or "Unknown vendor"
        conf = body.get("confidence")
        summary = (body.get("summary") or f"{vendor} · {invoice.currency} {amount:,.2f}")[:200]
        # Risk: high-value OR low-confidence surfaces for closer review.
        risk = "high" if (amount >= self._APPROVAL_HIGH_RISK_MIN or (conf is not None and conf < 40)) else "low"

        self._guard_invoice_date(invoice)
        invoice.status = InvoiceStatus.PENDING_APPROVAL.value
        task_service.enqueue(
            db, type="invoice-approval", source_ref=f"invoice:{invoice.id}",
            title=f"Approve invoice — {vendor} · {invoice.currency} {amount:,.2f}",
            summary=summary, body=body, risk=risk,
            amount=invoice.total_amount, currency=invoice.currency,
            assignee_user_id=approver_id, assignee_role=assignee_role,
            created_by="invoice-submit",
        )
        db.commit()
        db.refresh(invoice)
        return {
            "status": InvoiceStatus.PENDING_APPROVAL.value,
            "message": message,
            "assigned_to": approver_email if approver_id else assignee_role,
            "invoice": _invoice_dict(invoice, db),
        }

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
