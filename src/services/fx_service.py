"""
FX conversion service (POL-25 / POL-26).

Booking-time conversion into the entity's functional currency using the
monthly standard rate table. Statement actuals (both legs known) override at
the call sites that have them — this service is the standard-rate fallback.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy.orm import Session

from src.models.fx_rate import FinanceFxRate


class FxService:

    def get_monthly_rate(
        self, db: Session, from_currency: str, to_currency: str, on_date: date,
    ) -> Optional[Decimal]:
        """Standard rate for the transaction's month. Same currency → 1.
        Falls back to the inverse pair when only the reverse row exists."""
        if from_currency == to_currency:
            return Decimal("1")
        ym = on_date.strftime("%Y-%m")
        row = (db.query(FinanceFxRate)
                 .filter(FinanceFxRate.year_month == ym,
                         FinanceFxRate.from_currency == from_currency,
                         FinanceFxRate.to_currency == to_currency)
                 .first())
        if row:
            return Decimal(row.rate)
        inverse = (db.query(FinanceFxRate)
                     .filter(FinanceFxRate.year_month == ym,
                             FinanceFxRate.from_currency == to_currency,
                             FinanceFxRate.to_currency == from_currency)
                     .first())
        if inverse and inverse.rate:
            return (Decimal("1") / Decimal(inverse.rate)).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP)
        return None

    def to_functional(
        self, db: Session, amount_abs: Decimal, currency: str,
        functional_currency: str, on_date: date,
    ) -> tuple[Decimal, Decimal]:
        """(functional_amount_abs, rate). Raises when no rate is on file —
        booking a foreign amount unconverted would corrupt the ledger (the
        pre-POL-25 defect), so we refuse loudly instead."""
        rate = self.get_monthly_rate(db, currency, functional_currency, on_date)
        if rate is None:
            raise ValueError(
                f"No FX rate on file for {currency}->{functional_currency} "
                f"in {on_date.strftime('%Y-%m')} — add it to finance_fx_rates "
                f"before booking this transaction (POL-26 monthly standard rate).")
        functional = (amount_abs * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return functional, rate


fx_service = FxService()
