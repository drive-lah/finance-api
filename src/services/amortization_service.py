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
from src.models.invoice import FinanceInvoice
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

    # ── THE SCHEDULED-POSTINGS ENGINE (DA-13, Gaurav 2026-08-17) ─────────────
    # Sibling of the categorization engine: that one turns BANK TRANSACTIONS into journals,
    # this one turns SCHEDULES into journals. One call runs every pending pass in order,
    # idempotently, posting only months that have ARRIVED. Use at month-lock and in year passes.
    def run_all(self, db: Session, as_of_date: date | None = None) -> dict:
        if as_of_date is None:
            as_of_date = date.today()
        adjustments = self.apply_asset_adjustments(db, as_of_date=as_of_date)
        assets = self.run(db, as_of_date=as_of_date)
        prepaids = self.run_prepaids(db, as_of_date=as_of_date)
        return {
            "as_of_date": as_of_date.isoformat(),
            "adjustments": adjustments,
            "assets": assets,
            "prepaids": prepaids,
            "total_months_posted": (assets.get("months_posted") or 0) + (prepaids.get("months_posted") or 0),
            "errors": (assets.get("errors") or []) + (prepaids.get("errors") or []),
        }

    # ── Mid-life asset events (Gaurav 2026-08-17, DA-T5) ─────────────────────
    # A CREDIT posted to a policy-covered asset account = the asset shrank (refund,
    # write-down, disposal). The register must follow PROSPECTIVELY: reduce the base,
    # recompute the remaining monthly charge over the months still to run, and mark
    # the asset disposed when nothing is left. Idempotent: each adjusting JE is
    # applied once (tracked by source_schedule_id on that JE).
    def apply_asset_adjustments(self, db: Session, as_of_date: date | None = None) -> dict:
        if as_of_date is None:
            as_of_date = date.today()
        applied, skipped = 0, 0
        policies = db.query(FinanceCOAAmortizationPolicy).filter_by(is_active=True).all()
        for pol in policies:
            rows = (db.query(FinanceJournalEntry, FinanceJournalLine)
                    .join(FinanceJournalLine, FinanceJournalLine.entry_id == FinanceJournalEntry.id)
                    .filter(FinanceJournalLine.account_code == pol.asset_account_code,
                            FinanceJournalLine.credit_amount > 0,
                            FinanceJournalEntry.entry_date <= as_of_date,
                            FinanceJournalEntry.source != "amortization_scheduler",
                            FinanceJournalEntry.source_schedule_id.is_(None))
                    .all())
            for je, line in rows:
                sched = (db.query(FinanceAssetSchedule)
                         .filter(FinanceAssetSchedule.policy_id == pol.id,
                                 FinanceAssetSchedule.entity_id == je.entity_id,
                                 FinanceAssetSchedule.status == "active")
                         .order_by(FinanceAssetSchedule.id.desc()).first())
                if sched is None:
                    skipped += 1
                    continue
                credit = float(line.credit_amount)
                new_total = round(float(sched.total_amount) - credit, 2)
                remaining = max(sched.months_total - sched.months_posted, 1)
                sched.total_amount = max(new_total, 0)
                sched.monthly_amount = round(max(new_total, 0) / remaining, 2)
                if new_total <= 0:
                    sched.status = "disposed"
                je.source_schedule_id = sched.id     # marks this adjustment as applied
                applied += 1
        db.commit()
        return {"as_of_date": as_of_date.isoformat(), "adjustments_applied": applied,
                "credits_without_open_asset": skipped}

    # ── Prepaid RELEASE pass (Gaurav 2026-08-17) ─────────────────────────────
    # Same verb as depreciation, different tables: finance_amortization_schedules
    # parks invoice spend in 1300 Prepayments at approval; this releases the months
    # that have ARRIVED into the intended expense account. Never posts future months.
    #   monthly JE: Dr <expense_account_code> / Cr <prepaid_account_code>
    # Idempotent via schedules.entries_posted; last month trues-up the rounding.
    def run_prepaids(self, db: Session, as_of_date: date | None = None) -> dict:
        from src.models.contract import FinanceAmortizationSchedule
        if as_of_date is None:
            as_of_date = date.today()
        rows = (db.query(FinanceAmortizationSchedule)
                .filter(FinanceAmortizationSchedule.entries_posted < FinanceAmortizationSchedule.months)
                .all())
        posted = 0
        errors: list[dict] = []
        for sc in rows:
            try:
                inv = db.get(FinanceInvoice, sc.invoice_id) if sc.invoice_id else None
                entity_id = inv.entity_id if inv is not None else None
                if entity_id is None:
                    errors.append({"schedule_id": sc.id, "error": "no entity (orphan schedule)"})
                    continue
                while sc.entries_posted < sc.months:
                    due_month = _add_months(sc.start_month, sc.entries_posted)
                    if due_month > as_of_date:
                        break          # the month has not arrived — never post ahead
                    amount = float(sc.monthly_amount)
                    if sc.entries_posted == sc.months - 1:      # true-up the last month
                        amount = round(float(sc.total_amount)
                                       - float(sc.monthly_amount) * sc.entries_posted, 2)
                    if amount <= 0:
                        sc.entries_posted = sc.months
                        break
                    desc = (f"Prepaid release: invoice #{sc.invoice_id} "
                            f"({sc.entries_posted + 1}/{sc.months})")
                    je = journal_service.create(
                        db=db, entity_id=entity_id, entry_date=due_month, description=desc,
                        lines=[
                            {"account_code": sc.expense_account_code, "debit_amount": amount,
                             "credit_amount": 0.0, "description": desc},
                            {"account_code": sc.prepaid_account_code, "debit_amount": 0.0,
                             "credit_amount": amount, "description": desc},
                        ])
                    je.source = "prepaid_release"
                    je.source_prepaid_schedule_id = sc.id
                    db.flush()
                    sc.entries_posted += 1
                    posted += 1
                db.commit()          # durable per schedule: a later failure can't undo this one
            except Exception as e:
                # A failed schedule must not poison the batch — but a rollback would also
                # discard the source tags of JEs already flushed in this loop, orphaning them
                # (2026-08-17: that orphaning caused a double-release on the retry). So commit
                # the good work first, then continue with a clean session state.
                db.rollback()
                errors.append({"schedule_id": sc.id, "error": str(e)[:200]})
                # re-sync the cursor from what actually survived in the DB
                db.expire_all()
                continue
        db.commit()
        return {"as_of_date": as_of_date.isoformat(), "schedules_checked": len(rows),
                "months_posted": posted, "errors": errors}


amortization_service = AmortizationService()
