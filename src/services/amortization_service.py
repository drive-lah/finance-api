"""
Amortization / Depreciation Service

Handles two responsibilities:
1. check_and_create_schedule(db, transaction, je)
   Called from transaction_service.approve() after a transaction moves to
   RECONCILED. Inspects the JE's debit lines and, if any debit account code
   matches an active FinanceCOAAmortizationPolicy, creates a
   FinanceAssetSchedule for that transaction.

2. run(db, as_of_date)
   Monthly scheduler endpoint. Finds all active schedules with months due
   on or before as_of_date and posts the corresponding journal entries:
       Dr expense_account_code / Cr accumulated_account_code
"""
import logging
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from src.models.depreciation import FinanceCOAAmortizationPolicy, FinanceAssetSchedule
from src.models.journal_entry import FinanceJournalEntry
from src.models.journal_line import FinanceJournalLine
from src.services.journal_service import journal_service

logger = logging.getLogger(__name__)


def _first_of_next_month(d: date) -> date:
    """Return the first day of the month after d."""
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _add_months(d: date, n: int) -> date:
    """Return d + n calendar months (clamped to last day of month)."""
    import calendar
    month = d.month + n
    year = d.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class AmortizationService:
    """Service for COA-policy-driven depreciation and amortization scheduling."""

    def _find_policy(
        self,
        db: Session,
        account_code: str,
        entity_id: int,
    ) -> FinanceCOAAmortizationPolicy | None:
        """
        Look up the best-matching active policy for an account_code + entity.

        Entity-specific policy (entity_id matches) wins over global (entity_id NULL).
        Returns None if no active policy exists.
        """
        # Entity-specific first
        policy = (
            db.query(FinanceCOAAmortizationPolicy)
            .filter(
                FinanceCOAAmortizationPolicy.asset_account_code == account_code,
                FinanceCOAAmortizationPolicy.entity_id == entity_id,
                FinanceCOAAmortizationPolicy.is_active.is_(True),
            )
            .first()
        )
        if policy:
            return policy

        # Global fallback
        return (
            db.query(FinanceCOAAmortizationPolicy)
            .filter(
                FinanceCOAAmortizationPolicy.asset_account_code == account_code,
                FinanceCOAAmortizationPolicy.entity_id.is_(None),
                FinanceCOAAmortizationPolicy.is_active.is_(True),
            )
            .first()
        )

    def check_and_create_schedule(
        self,
        db: Session,
        transaction: "FinanceTransaction",  # type: ignore[name-defined]
        je: FinanceJournalEntry,
    ) -> FinanceAssetSchedule | None:
        """
        Inspect a newly-reconciled JE for debit lines that match an active policy.

        If a match is found and no schedule already exists for this transaction,
        a FinanceAssetSchedule is created and flushed (not committed — caller commits).

        Returns the created schedule, or None if no policy matched.
        """
        # Avoid creating a second schedule if one already exists for this transaction
        existing = (
            db.query(FinanceAssetSchedule)
            .filter(FinanceAssetSchedule.transaction_id == transaction.id)
            .first()
        )
        if existing:
            return None

        # Load JE lines
        lines = (
            db.query(FinanceJournalLine)
            .filter(FinanceJournalLine.entry_id == je.id)
            .all()
        )

        entity_id = je.entity_id

        for line in lines:
            debit = float(line.debit_amount) if line.debit_amount else 0.0
            if debit <= 0:
                continue

            policy = self._find_policy(db, line.account_code, entity_id)
            if not policy:
                continue

            # Found a match — build the schedule
            total = round(debit, 2)
            months = policy.useful_life_months
            monthly = round(total / months, 2)

            # Start date: first day of the month after the transaction date
            start_date = _first_of_next_month(transaction.transaction_date)

            schedule = FinanceAssetSchedule(
                policy_id=policy.id,
                transaction_id=transaction.id,
                journal_entry_id=je.id,
                entity_id=entity_id,
                asset_description=transaction.description,
                total_amount=total,
                monthly_amount=monthly,
                months_total=months,
                months_posted=0,
                start_date=start_date,
                status="active",
            )
            db.add(schedule)
            db.flush()

            logger.info(
                f"Created {policy.policy_type} schedule {schedule.id} "
                f"for transaction {transaction.id}: "
                f"{total} over {months} months @ {monthly}/mo "
                f"starting {start_date}"
            )
            return schedule  # one schedule per transaction (first matching debit line)

        return None

    def run(
        self,
        db: Session,
        as_of_date: date | None = None,
    ) -> dict:
        """
        Post all due depreciation/amortization JEs for active schedules.

        For each active schedule, the months due are:
            schedule.start_date + months_posted * 1 month  ≤  as_of_date

        Each due month creates one JE:
            Dr expense_account_code / Cr accumulated_account_code

        Returns a summary dict with counts and any errors.
        """
        if as_of_date is None:
            as_of_date = date.today()

        active_schedules = (
            db.query(FinanceAssetSchedule)
            .filter(FinanceAssetSchedule.status == "active")
            .all()
        )

        posted_count = 0
        skipped_count = 0
        errors: list[dict] = []

        for schedule in active_schedules:
            # Load the policy to get expense and accumulated accounts
            policy = db.get(FinanceCOAAmortizationPolicy, schedule.policy_id)
            if not policy:
                errors.append({
                    "schedule_id": schedule.id,
                    "error": "Policy not found",
                })
                continue

            while schedule.months_posted < schedule.months_total:
                # Date of the next due entry
                due_month = _add_months(schedule.start_date, schedule.months_posted)
                if due_month > as_of_date:
                    break  # not yet due

                # Determine amount: last month may differ due to rounding
                if schedule.months_posted == schedule.months_total - 1:
                    # Last month: post whatever remains to avoid rounding drift
                    already_posted = float(schedule.monthly_amount) * schedule.months_posted
                    amount = round(float(schedule.total_amount) - already_posted, 2)
                else:
                    amount = float(schedule.monthly_amount)

                if amount <= 0:
                    schedule.months_posted += 1
                    continue

                try:
                    description = (
                        f"{policy.policy_type.title()} — "
                        f"{schedule.asset_description or f'schedule {schedule.id}'} "
                        f"({schedule.months_posted + 1}/{schedule.months_total})"
                    )
                    je = journal_service.create(
                        db=db,
                        entity_id=schedule.entity_id,
                        entry_date=due_month,
                        description=description,
                        lines=[
                            {
                                "account_code": policy.expense_account_code,
                                "debit_amount": amount,
                                "credit_amount": 0.0,
                                "description": description,
                            },
                            {
                                "account_code": policy.accumulated_account_code,
                                "debit_amount": 0.0,
                                "credit_amount": amount,
                                "description": description,
                            },
                        ],
                    )
                    je.source = "amortization_scheduler"
                    je.source_schedule_id = schedule.id
                    db.flush()

                    schedule.months_posted += 1
                    posted_count += 1

                    if schedule.months_posted >= schedule.months_total:
                        schedule.status = "completed"
                        logger.info(f"Schedule {schedule.id} completed.")

                except Exception as e:
                    logger.error(
                        f"Failed to post month {schedule.months_posted + 1} "
                        f"for schedule {schedule.id}: {e}",
                        exc_info=True,
                    )
                    errors.append({
                        "schedule_id": schedule.id,
                        "month": schedule.months_posted + 1,
                        "error": str(e),
                    })
                    db.rollback()
                    break  # stop processing this schedule on error

            else:
                # While loop exhausted without break — mark completed if not already
                if schedule.months_posted >= schedule.months_total and schedule.status != "completed":
                    schedule.status = "completed"

        db.commit()

        return {
            "as_of_date": as_of_date.isoformat(),
            "schedules_checked": len(active_schedules),
            "months_posted": posted_count,
            "errors": errors,
        }


# Singleton instance
amortization_service = AmortizationService()
