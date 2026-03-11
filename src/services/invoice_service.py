"""
Invoice Service

Business logic for managing invoices in the Accounts Payable workflow.
Handles creation, approval (with JE generation), rejection, voiding,
payment recording, and AP knock-off lookups.
"""
import logging
from datetime import datetime, date, UTC
from typing import Optional

from sqlalchemy.orm import Session

from src.models.invoice import FinanceInvoice, InvoiceStatus
from src.models.contract import FinanceAmortizationSchedule
from src.models.schemas import InvoiceCreate, InvoiceUpdate
from src.services.journal_service import journal_service
from src.utils.errors import NotFoundError

logger = logging.getLogger(__name__)

# Standard AP liability account
AP_ACCOUNT_CODE = "2000"
# Prepaid asset account for amortization
PREPAID_ACCOUNT_CODE = "1200"


def _months_between(start: date, end: date) -> int:
    """Calculate the number of calendar months between two dates (inclusive)."""
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


class InvoiceService:
    """Service for managing invoices in the Accounts Payable system."""

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

        Optionally auto-matches against an existing contract for the
        same counterparty/entity/amount.
        """
        invoice = FinanceInvoice(
            entity_id=data.entity_id,
            counterparty_id=data.counterparty_id,
            contract_id=data.contract_id,
            invoice_number=data.invoice_number,
            invoice_date=data.invoice_date,
            due_date=data.due_date,
            total_amount=data.total_amount,
            currency=data.currency,
            contra_account_code=data.contra_account_code,
            service_period_start=data.service_period_start,
            service_period_end=data.service_period_end,
            uploaded_by=data.uploaded_by,
            notes=data.notes,
            pdf_s3_key=data.pdf_s3_key,
            status=InvoiceStatus.DRAFT.value,
        )

        # Try contract matching if counterparty is set but contract_id is not
        if data.counterparty_id and not data.contract_id:
            from src.services.contract_service import contract_service
            contract = contract_service.find_for_invoice(
                db, data.counterparty_id, data.entity_id, data.total_amount, data.currency,
            )
            if contract:
                invoice.contract_id = contract.id
                invoice.contract_matched = True
                if not invoice.contra_account_code and contract.coa_account_code:
                    invoice.contra_account_code = contract.coa_account_code

        db.add(invoice)
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

        db.commit()
        db.refresh(invoice)
        return invoice

    def approve(self, db: Session, invoice_id: int, approved_by: str) -> FinanceInvoice:
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

        if not invoice.contra_account_code:
            from src.utils.errors import BadRequestError
            raise BadRequestError(
                "Cannot approve invoice without a contra_account_code. "
                "Set the expense account before approving."
            )

        total = float(invoice.total_amount)
        needs_amortization = (
            invoice.service_period_start
            and invoice.service_period_end
            and _months_between(invoice.service_period_start, invoice.service_period_end) > 1
        )

        if needs_amortization:
            # Amortization: Dr Prepaid (1200) / Cr AP (2000)
            debit_code = PREPAID_ACCOUNT_CODE
        else:
            # Standard: Dr contra_account / Cr AP (2000)
            debit_code = invoice.contra_account_code

        lines = [
            {
                "account_code": debit_code,
                "debit_amount": total,
                "credit_amount": 0.0,
                "description": f"Invoice {invoice.invoice_number or invoice.id}",
            },
            {
                "account_code": AP_ACCOUNT_CODE,
                "debit_amount": 0.0,
                "credit_amount": total,
                "description": f"Invoice {invoice.invoice_number or invoice.id}",
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
    ) -> Optional[FinanceInvoice]:
        """
        Find the oldest open invoice for a counterparty matching the payment amount.

        Used by the AP knock-off phase of the categorization engine.
        Matches within +/-2% tolerance to handle FX rounding.
        """
        open_statuses = (InvoiceStatus.APPROVED.value, InvoiceStatus.PARTIALLY_PAID.value)

        invoices = (
            db.query(FinanceInvoice)
            .filter(
                FinanceInvoice.counterparty_id == counterparty_id,
                FinanceInvoice.currency == currency,
                FinanceInvoice.status.in_(open_statuses),
            )
            .order_by(FinanceInvoice.invoice_date.asc(), FinanceInvoice.id.asc())
            .all()
        )

        for inv in invoices:
            remaining = float(inv.total_amount) - float(inv.amount_paid)
            if remaining <= 0:
                continue
            tolerance = remaining * 0.02
            if abs(amount - remaining) <= tolerance:
                return inv

        return None

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


# Singleton instance
invoice_service = InvoiceService()
