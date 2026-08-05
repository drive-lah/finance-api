"""Finance Invoice ↔ Payment Match Model

Explicit link between an invoice and a bank payment (transaction). Replaces the
previously-implicit link (reconstructed only via the knock-off journal entry).

Two states:
  - provisional : a proposed/identified link (PAYLINE, reference, or a human drag).
                  Touches nothing in the ledger. Multiple candidates per invoice OK.
  - logged      : finalized. Requires BOTH invoice.status = approved AND
                  transaction.status = Reconciled, with a knock-off JE posted
                  (journal_entry_id set). 'logged' is DERIVED from those facts —
                  never hand-set.

amount_applied supports partial / bundled matches (one payment across several
invoices, or one invoice paid by several payments).
"""
from datetime import datetime
import enum

from sqlalchemy import String, DateTime, Integer, ForeignKey, Numeric, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class MatchState(str, enum.Enum):
    PROVISIONAL = "provisional"
    LOGGED = "logged"


class FinanceInvoicePaymentMatch(Base):
    __tablename__ = "finance_invoice_payment_matches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_invoices.id", ondelete="CASCADE"), nullable=False)
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finance_transactions.id", ondelete="CASCADE"), nullable=False)

    state: Mapped[str] = mapped_column(String(20), nullable=False, default=MatchState.PROVISIONAL.value)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True,
        comment="reference | amount_date | manual | ocr | reference+amount")
    confidence: Mapped[str | None] = mapped_column(String(8), nullable=True, comment="HIGH | MED | LOW")
    amount_applied: Mapped[float | None] = mapped_column(Numeric(precision=15, scale=2), nullable=True,
        comment="portion of the payment applied to this invoice (partial/bundled); NULL = full")

    # set only when logged
    journal_entry_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("finance_journal_entries.id", ondelete="SET NULL"), nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    logged_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    logged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_fipm_invoice", "invoice_id"),
        Index("ix_fipm_transaction", "transaction_id"),
        Index("ix_fipm_state", "state"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "invoice_id": self.invoice_id, "transaction_id": self.transaction_id,
            "state": self.state, "source": self.source, "confidence": self.confidence,
            "amount_applied": float(self.amount_applied) if self.amount_applied is not None else None,
            "journal_entry_id": self.journal_entry_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "logged_by": self.logged_by,
            "logged_at": self.logged_at.isoformat() if self.logged_at else None,
            "notes": self.notes,
        }
