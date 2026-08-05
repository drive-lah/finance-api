"""Employee Claim model (use cases #5, #6).

An employee submits an expense claim + receipt; it routes to their MANAGER (org hierarchy
from users.manager_id) for approval; on approval a bill JE is posted (Dr claim-COA /
Cr 2303 Employee Claims Payable); reimbursement later runs through the payout rails.

Own-scoped (finance.expenses:own): an employee sees only their OWN claims; their manager
sees claims awaiting their approval; admin sees all. `owner_user_id` is the scope key.
"""
from datetime import datetime
import enum

from sqlalchemy import String, DateTime, Integer, ForeignKey, Numeric, Text, Index, Date
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class ClaimStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"       # awaiting manager approval
    APPROVED = "approved"         # bill JE posted; payable
    REJECTED = "rejected"
    PAID = "paid"                 # reimbursed


# Claim category → expense COA (Employee Claims range)
CATEGORY_COA = {
    "travel": "6010", "meals": "6011", "transport": "6012",
    "office_supplies": "6013", "other": "6014",
}

EMPLOYEE_CLAIMS_PAYABLE = "2303"


class FinanceEmployeeClaim(Base):
    __tablename__ = "finance_employee_claims"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # owner/submitter — the scope key for own-scoping
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_entities.id"), nullable=False)
    manager_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # approver

    amount: Mapped[float] = mapped_column(Numeric(precision=15, scale=2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="SGD")
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    coa_account_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    expense_date: Mapped[Date | None] = mapped_column(Date, nullable=True)

    # optional operational context
    trip_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intercom_ticket_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    receipt_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ClaimStatus.DRAFT.value)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    journal_entry_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("finance_journal_entries.id", ondelete="SET NULL"), nullable=True)
    payout_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_fec_owner", "owner_user_id"),
        Index("ix_fec_manager", "manager_user_id"),
        Index("ix_fec_status", "status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "owner_user_id": self.owner_user_id, "entity_id": self.entity_id,
            "manager_user_id": self.manager_user_id,
            "amount": float(self.amount) if self.amount is not None else None,
            "currency": self.currency, "category": self.category,
            "coa_account_code": self.coa_account_code, "description": self.description,
            "expense_date": self.expense_date.isoformat() if self.expense_date else None,
            "trip_id": self.trip_id, "intercom_ticket_id": self.intercom_ticket_id,
            "receipt_s3_key": self.receipt_s3_key, "receipt_filename": self.receipt_filename,
            "status": self.status,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejected_by": self.rejected_by, "rejection_reason": self.rejection_reason,
            "journal_entry_id": self.journal_entry_id, "payout_id": self.payout_id,
            "transaction_id": self.transaction_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
