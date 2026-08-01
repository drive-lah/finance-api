"""
Monthly standard FX rates (POL-26).

One row per (year_month, from_currency, to_currency): the standard rate set on
the 1st of the month from a public mid-rate. All foreign-currency bookings in
that month convert at this rate — except transfers/conversions where the
statement shows both legs (the actuals override, per POL-26 ②).
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, DateTime, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class FinanceFxRate(Base):
    __tablename__ = "finance_fx_rates"
    __table_args__ = (
        UniqueConstraint("year_month", "from_currency", "to_currency",
                         name="uq_fx_rates_month_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    year_month: Mapped[str] = mapped_column(String(7), nullable=False)   # "2026-01"
    from_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    to_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (f"<FxRate {self.year_month} {self.from_currency}->"
                f"{self.to_currency} @ {self.rate}>")
