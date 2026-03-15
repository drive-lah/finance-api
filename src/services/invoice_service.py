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
from typing import TYPE_CHECKING, Optional

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

logger = logging.getLogger(__name__)

# Standard AP liability account
AP_ACCOUNT_CODE = "2000"
# Prepaid asset account for amortization
PREPAID_ACCOUNT_CODE = "1200"
# GST / VAT input tax credit (recoverable on purchases)
GST_INPUT_ACCOUNT_CODE = "1350"

# ── Intercompany AP account codes ─────────────────────────────────────────────
# Keyed by (bank_entity_short_name, invoice_entity_short_name).
# Short name = last word of FinanceEntity.name (e.g., "DL SG" → "SG").
_IC_RECEIVABLE_CODES: dict[tuple[str, str], str] = {
    ("SG", "AU"):       "8000",  # SG books: IC Due from AU
    ("SG", "Ventures"): "8001",  # SG books: IC Due from Ventures
    ("AU", "SG"):       "8010",  # AU books: IC Due from SG
    ("AU", "Ventures"): "8011",  # AU books: IC Due from Ventures
    ("Ventures", "SG"): "8020",  # Ventures books: IC Due from SG
    ("Ventures", "AU"): "8021",  # Ventures books: IC Due from AU
}
_IC_PAYABLE_CODES: dict[tuple[str, str], str] = {
    ("SG", "AU"):       "8100",  # SG books: IC Due to AU
    ("SG", "Ventures"): "8101",  # SG books: IC Due to Ventures
    ("AU", "SG"):       "8110",  # AU books: IC Due to SG
    ("AU", "Ventures"): "8111",  # AU books: IC Due to Ventures
    ("Ventures", "SG"): "8120",  # Ventures books: IC Due to SG
    ("Ventures", "AU"): "8121",  # Ventures books: IC Due to AU
}


def _entity_short(name: str) -> str:
    """Extract the short entity identifier from a full name: 'DL SG' → 'SG'."""
    return name.strip().rsplit(" ", 1)[-1]


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

    def get_all(
        self,
        db: Session,
        entity_id: Optional[int] = None,
        status: Optional[str] = None,
        counterparty_id: Optional[int] = None,
    ) -> list[FinanceInvoice]:
        """Retrieve invoices with optional filtering."""
        query = db.query(FinanceInvoice)
        if entity_id is not None:
            query = query.filter(FinanceInvoice.entity_id == entity_id)
        if status is not None:
            query = query.filter(FinanceInvoice.status == status)
        if counterparty_id is not None:
            query = query.filter(FinanceInvoice.counterparty_id == counterparty_id)
        return query.order_by(FinanceInvoice.invoice_date.desc(), FinanceInvoice.id.desc()).all()

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
        # Semantic duplicate check
        if data.counterparty_id and data.invoice_number:
            existing = (
                db.query(FinanceInvoice)
                .filter(
                    FinanceInvoice.entity_id == data.entity_id,
                    FinanceInvoice.counterparty_id == data.counterparty_id,
                    FinanceInvoice.invoice_number == data.invoice_number,
                    FinanceInvoice.invoice_date == data.invoice_date,
                    FinanceInvoice.currency == data.currency,
                )
                .first()
            )
            if existing:
                from src.utils.errors import ConflictError
                raise ConflictError(
                    f"Invoice {data.invoice_number} from this vendor on {data.invoice_date} "
                    f"already exists (ID {existing.id}, status: {existing.status})."
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

        # ── COA priority: DB counterparty → contract → AI suggestion ──
        coa_code: Optional[str] = None
        coa_source: Optional[str] = None

        # 1. Counterparty default_account_code
        if data.counterparty_id:
            cp = db.get(FinanceCounterparty, data.counterparty_id)
            if cp and cp.default_account_code:
                coa_code = cp.default_account_code
                coa_source = "db"

        # 2. Contract COA (after contract matching below)
        # will be applied post-match

        # 3. AI suggestion passed in contra_account_code
        ai_coa = data.contra_account_code

        # ── Contract matching ──
        if data.counterparty_id and not data.contract_id:
            from src.services.contract_service import contract_service
            contract = contract_service.find_for_invoice(
                db, data.counterparty_id, data.entity_id, data.total_amount, data.currency,
            )
            if contract:
                invoice.contract_id = contract.id
                invoice.contract_matched = True
                if not coa_code and contract.coa_account_code:
                    coa_code = contract.coa_account_code
                    coa_source = "contract"

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
            db.rollback()
            from src.utils.errors import ConflictError
            raise ConflictError("Duplicate invoice detected (unique constraint violation).")
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

        db.commit()
        db.refresh(invoice)
        return invoice

    def approve(self, db: Session, invoice_id: int, approved_by: str, contra_account_code: Optional[str] = None) -> FinanceInvoice:
        """
        Approve an invoice, creating the corresponding journal entry.

        Standard case: Dr contra_account / Cr 2000 (Accounts Payable)
        Amortization case: Dr 1200 (Prepaid) / Cr 2000, plus amortization schedule
        """
        invoice = self.get_by_id(db, invoice_id)

        if invoice.status not in (InvoiceStatus.DRAFT.value, InvoiceStatus.PENDING_APPROVAL.value):
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Cannot approve invoice in '{invoice.status}' status. "
                f"Only draft or pending_approval invoices can be approved."
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
        net = float(invoice.net_amount) if invoice.net_amount else (total - tax)

        needs_amortization = (
            invoice.service_period_start
            and invoice.service_period_end
            and _months_between(invoice.service_period_start, invoice.service_period_end) > 1
        )

        if needs_amortization:
            debit_code = PREPAID_ACCOUNT_CODE
        else:
            debit_code = invoice.contra_account_code

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
                    "account_code": AP_ACCOUNT_CODE,
                    "debit_amount": 0.0,
                    "credit_amount": round(total, 2),
                    "description": inv_ref,
                },
            ]
        else:
            # Standard 2-line JE: Dr expense / Cr AP
            lines = [
                {
                    "account_code": debit_code,
                    "debit_amount": total,
                    "credit_amount": 0.0,
                    "description": inv_ref,
                },
                {
                    "account_code": AP_ACCOUNT_CODE,
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

    def reject(self, db: Session, invoice_id: int, rejection_reason: str) -> FinanceInvoice:
        """Reject an invoice with a reason."""
        invoice = self.get_by_id(db, invoice_id)

        if invoice.status not in (InvoiceStatus.DRAFT.value, InvoiceStatus.PENDING_APPROVAL.value):
            from src.utils.errors import ConflictError
            raise ConflictError(
                f"Cannot reject invoice in '{invoice.status}' status."
            )

        invoice.status = InvoiceStatus.REJECTED.value
        invoice.rejection_reason = rejection_reason
        db.commit()
        db.refresh(invoice)
        return invoice

    def void(self, db: Session, invoice_id: int) -> FinanceInvoice:
        """Void an invoice. Only draft, pending_approval, or rejected invoices can be voided."""
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
        Find the best open invoice to knock off for a counterparty payment.

        Ranked matching (first match wins in priority order):
          1. Reference match  — invoice_number appears in bank description or reference_number
          2. Exact amount     — payment ≈ remaining balance (±2% for FX rounding)
          3. Partial payment  — payment < remaining (accepted; creates PARTIALLY_PAID record)

        Within each tier, oldest invoice (by invoice_date, then id) wins (FIFO convention).

        Date constraint: invoices dated after transaction_date are excluded — a payment
        cannot precede the invoice it settles.
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

        # Tier 1: reference match — invoice_number found in bank text
        for inv in invoices:
            remaining = float(inv.total_amount) - float(inv.amount_paid)
            if remaining <= 0:
                continue
            if inv.invoice_number:
                inv_num_upper = inv.invoice_number.upper()
                if inv_num_upper and (
                    inv_num_upper in desc_upper or inv_num_upper in ref_upper
                ):
                    return inv

        # Tier 2: exact amount match (payment ≈ remaining ±2%)
        for inv in invoices:
            remaining = float(inv.total_amount) - float(inv.amount_paid)
            if remaining <= 0:
                continue
            if abs(amount - remaining) <= remaining * 0.02:
                return inv

        # Tier 3: partial payment (payment < remaining, more than zero)
        for inv in invoices:
            remaining = float(inv.total_amount) - float(inv.amount_paid)
            if remaining <= 0:
                continue
            if 0 < amount < remaining * 1.02:
                return inv

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

        if bank_entity_id == invoice_entity_id:
            # ── Same-entity: single 2-line JE ──────────────────────────────
            entry = journal_service.create(
                db=db,
                entity_id=bank_entity_id,
                entry_date=txn_date,
                description=description,
                lines=[
                    {
                        "account_code": AP_ACCOUNT_CODE,
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
        bank_entry.intercompany_group_id = ic_group_id

        # Invoice entity: Dr 2000 AP / Cr IC Payable
        inv_entry = journal_service.create(
            db=db,
            entity_id=invoice_entity_id,
            entry_date=txn_date,
            description=description,
            lines=[
                {
                    "account_code": AP_ACCOUNT_CODE,
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
        inv_entry.intercompany_group_id = ic_group_id

        db.flush()
        return bank_entry

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


    def submit(self, db: Session, invoice_id: int, confirmed: bool = False) -> dict:
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
        if missing:
            from src.utils.errors import BadRequestError
            raise BadRequestError(
                f"Cannot submit invoice — missing required fields: {', '.join(missing)}"
            )

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

        new_status, auto_approved_by = self._evaluate_approval_rules(db, invoice)

        if new_status == InvoiceStatus.APPROVED.value:
            db.commit()  # flush before approve()
            updated = self.approve(db, invoice_id, approved_by=auto_approved_by or "auto")
            message = "Invoice auto-approved via approval rule"
        else:
            invoice.status = new_status
            db.commit()
            db.refresh(invoice)
            updated = invoice
            message = "Invoice marked for approval (no matching auto-approve rule)"

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
            response_text = message.content[0].text.strip()
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
                FinanceApprovalRule.is_active == True,
            )
            .order_by(FinanceApprovalRule.priority.asc())
            .all()
        )

        amount = float(invoice.total_amount)

        for rule in rules:
            # Amount range check
            if rule.min_amount is not None and amount < float(rule.min_amount):
                continue
            if rule.max_amount is not None and amount > float(rule.max_amount):
                continue
            # Currency check
            if rule.currency and rule.currency != invoice.currency:
                continue
            # COA prefix check
            if rule.coa_account_prefix and invoice.contra_account_code:
                if not invoice.contra_account_code.startswith(rule.coa_account_prefix):
                    continue
            elif rule.coa_account_prefix and not invoice.contra_account_code:
                continue
            # Counterparty type check
            if rule.counterparty_type and invoice.counterparty_id:
                from src.models.counterparty import FinanceCounterparty
                cp = db.get(FinanceCounterparty, invoice.counterparty_id)
                if cp and cp.counterparty_type != rule.counterparty_type:
                    continue

            # Rule matched
            if rule.action == "auto_approve":
                return InvoiceStatus.APPROVED.value, f"auto:{rule.name}"
            else:
                return InvoiceStatus.PENDING_APPROVAL.value, None

        # No rule matched → require approval
        return InvoiceStatus.PENDING_APPROVAL.value, None


# Singleton instance
invoice_service = InvoiceService()
