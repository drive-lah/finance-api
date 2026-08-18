"""
COA Amortization/Depreciation Models

finance_coa_amortization_policies — policy table mapping asset COA codes
  to their contra-asset and expense accounts + useful life.

finance_asset_schedules — one per capitalisation event (reconciled transaction
  that hit a policy-covered account). The scheduler walks active schedules and
  posts monthly JEs.
"""
from datetime import datetime, date
from typing import Optional

from sqlalchemy import String, DateTime, Date, Integer, Boolean, Numeric, Text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class FinanceCOAAmortizationPolicy(Base):
    """
    Defines which balance-sheet COA codes trigger automatic depreciation /
    amortization scheduling when a transaction is reconciled to them.

    Entity-scoped (entity_id set) policies take priority over global ones
    (entity_id NULL) when both match the same account code.
    """
    __tablename__ = "finance_coa_amortization_policies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    asset_account_code: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="Balance-sheet account that triggers this policy (e.g. '1710')",
    )
    accumulated_account_code: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="Contra-asset accumulated depr/amort account (e.g. '1810')",
    )
    expense_account_code: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="P&L charge account for the periodic entry (e.g. '7400')",
    )
    useful_life_months: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Total months over which to spread the cost",
    )
    policy_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="amortization",
        comment="'amortization' (intangibles/prepaid) or 'depreciation' (fixed assets)",
    )
    method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="straight_line",
        comment="Calculation method — only 'straight_line' supported currently",
    )
    entity_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=True,
        comment="NULL = global; set for entity-specific override",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )

    __table_args__ = (
        Index("ix_finance_coa_amort_policies_code", "asset_account_code"),
        Index("ix_finance_coa_amort_policies_entity", "entity_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<FinanceCOAAmortizationPolicy(id={self.id}, "
            f"asset={self.asset_account_code}, months={self.useful_life_months})>"
        )


class FinanceAssetSchedule(Base):
    """
    One record per capitalisation event.

    Created in transaction_service.approve() when the reconciliation JE has
    a debit line matching an active FinanceCOAAmortizationPolicy.

    The monthly scheduler reads active schedules and creates one JE per due month:
        Dr expense_account_code / Cr accumulated_account_code
    """
    __tablename__ = "finance_asset_schedules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    policy_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_coa_amortization_policies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    transaction_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_transactions.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
        comment="The bank transaction that triggered this schedule, when there was one. NULL for "
                "journal-born assets — invoice approvals and manual journals carry no bank line, "
                "and refusing them meant that spend never depreciated (DA-15).",
    )
    journal_entry_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("finance_journal_entries.id", ondelete="SET NULL"),
        nullable=True,
        comment="Reconciliation JE that capitalised the asset",
    )
    entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("finance_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    total_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    monthly_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    months_total: Mapped[int] = mapped_column(Integer, nullable=False)
    months_posted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_date: Mapped[date] = mapped_column(
        Date, nullable=False,
        comment="First day of the first depreciation/amortization month",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active",
        comment="'active' | 'completed' | 'cancelled'",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )

    __table_args__ = (
        Index("ix_finance_asset_schedules_status", "status"),
        Index("ix_finance_asset_schedules_entity", "entity_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<FinanceAssetSchedule(id={self.id}, "
            f"txn={self.transaction_id}, posted={self.months_posted}/{self.months_total})>"
        )
