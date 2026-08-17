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

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from src.models.depreciation import FinanceCOAAmortizationPolicy, FinanceAssetSchedule
from src.models.journal_entry import FinanceJournalEntry
from src.models.journal_line import FinanceJournalLine
from src.models.invoice import FinanceInvoice
from src.services.journal_service import journal_service

logger = logging.getLogger(__name__)

# Journals this engine itself writes. Registration must NEVER treat one of its own postings as
# a newly purchased asset (DA-14, 2026-08-17): a prepaid release that debits an asset account —
# which is exactly what the mis-coded 1710 schedules did — would otherwise be registered as a
# fresh capital purchase and depreciated a second time.
_SCHEDULED_SOURCES = ("amortization_scheduler", "prepaid_release")

# The one prepaid parking account (COA 1300 Prepayments; 1200 is Trade Receivables).
# Defined here and imported by invoice_service so the code lives in exactly one place.
PREPAID_ACCOUNT_CODE = "1300"


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

    # ── PASS 0: REGISTER anything capitalized but not yet in the register ─────
    # (Gaurav 2026-08-17: "that engine should just run" — registration is a PASS of the
    # engine, not a one-off backfill script.) check_and_create_schedule fires at transaction
    # APPROVAL, so anything booked another way — history years finalized in bulk, manual
    # journals, imports — never registers and can never age. This pass sweeps every
    # policy-covered account for debits with no register row and registers them. Idempotent:
    # a JE already linked to a schedule is skipped, so it is safe to run every month.
    def register_pending(self, db: Session, as_of_date: date | None = None,
                         entity_ids: list[int] | None = None) -> dict:
        from src.models.transaction import FinanceTransaction
        if as_of_date is None:
            as_of_date = date.today()
        created, skipped = 0, 0
        policies = db.query(FinanceCOAAmortizationPolicy).filter_by(is_active=True).all()
        for pol in policies:
            q = (db.query(FinanceJournalEntry, FinanceJournalLine)
                 .join(FinanceJournalLine, FinanceJournalLine.entry_id == FinanceJournalEntry.id)
                 .filter(FinanceJournalLine.account_code == pol.asset_account_code,
                         FinanceJournalLine.debit_amount > 0,
                         FinanceJournalEntry.entry_date <= as_of_date,
                         FinanceJournalEntry.status.in_(["POSTED", "DRAFT"]),
                         # SQL three-valued logic: `source NOT IN (...)` is NULL — not TRUE —
                         # when source IS NULL, so a plain notin_ silently drops every MANUAL
                         # journal, the exact case this sweep exists to catch (DA-15).
                         or_(FinanceJournalEntry.source.is_(None),
                             FinanceJournalEntry.source.notin_(_SCHEDULED_SOURCES))))
            if entity_ids:
                q = q.filter(FinanceJournalEntry.entity_id.in_(entity_ids))
            for je, line in q.all():
                if pol.entity_id is not None and pol.entity_id != je.entity_id:
                    continue
                exists = (db.query(FinanceAssetSchedule)
                          .filter(FinanceAssetSchedule.journal_entry_id == je.id).first())
                if exists:
                    skipped += 1
                    continue
                # DA-15 (Gaurav 2026-08-18): a bank transaction is EVIDENCE, not a requirement.
                # The journal already carries amount, date, entity and description, so a
                # journal-born asset (invoice approval, manual capitalization) registers too.
                # Requiring the bank line meant invoice-bought capital never depreciated.
                txn = (db.query(FinanceTransaction)
                       .filter(FinanceTransaction.reconciled_journal_entry_id == je.id).first())
                total = round(float(line.debit_amount), 2)
                months = pol.useful_life_months
                sched = FinanceAssetSchedule(
                    policy_id=pol.id, transaction_id=(txn.id if txn else None),
                    journal_entry_id=je.id,
                    entity_id=je.entity_id,
                    asset_description=(je.description or f"Asset via JE {je.id}")[:255],
                    total_amount=total, monthly_amount=round(total / months, 2),
                    months_total=months, months_posted=0,
                    start_date=_first_of_next_month(je.entry_date), status="active")
                db.add(sched)
                db.flush()
                created += 1
                logger.info(f"register_pending: registered asset {sched.id} from JE {je.id} "
                            f"({pol.asset_account_code} {total} over {months}mo)")
        db.commit()
        return {"as_of_date": as_of_date.isoformat(), "registered": created,
                "already_registered_or_skipped": skipped}

    def register_from_journal(self, db: Session, je: FinanceJournalEntry) -> FinanceAssetSchedule | None:
        """Register capitalized spend straight off a journal — no bank transaction needed (DA-15).

        Door A (invoice approval) and any other journal-born capitalization call this so the asset
        starts depreciating the moment it is booked, instead of waiting for a sweep that used to
        refuse it outright. Idempotent on journal_entry_id. Flushes; the caller commits.
        """
        if (je.source or "") in _SCHEDULED_SOURCES:
            return None
        existing = (db.query(FinanceAssetSchedule)
                    .filter(FinanceAssetSchedule.journal_entry_id == je.id).first())
        if existing:
            return None
        lines = (db.query(FinanceJournalLine)
                 .filter(FinanceJournalLine.entry_id == je.id).all())
        for line in lines:
            debit = float(line.debit_amount or 0)
            if debit <= 0:
                continue
            policy = self._find_policy(db, line.account_code, je.entity_id)
            if not policy:
                continue
            total = round(debit, 2)
            months = policy.useful_life_months
            sched = FinanceAssetSchedule(
                policy_id=policy.id, transaction_id=None, journal_entry_id=je.id,
                entity_id=je.entity_id,
                asset_description=(je.description or f"Asset via JE {je.id}")[:255],
                total_amount=total, monthly_amount=round(total / months, 2),
                months_total=months, months_posted=0,
                start_date=_first_of_next_month(je.entry_date), status="active")
            db.add(sched)
            db.flush()
            logger.info(f"register_from_journal: registered asset {sched.id} from JE {je.id} "
                        f"({line.account_code} {total} over {months}mo)")
            return sched
        return None

    def unscheduled_prepaids(self, db: Session, as_of_date: date,
                             entity_ids: list[int] | None = None) -> list[dict]:
        """Debits parked in 1300 Prepayments that no release schedule answers for (DA-15).

        The asset side can self-heal because a policy supplies the useful life. The prepaid side
        cannot: a spread needs a SERVICE PERIOD, and only the invoice knows it — the engine would
        have to invent one. So the fix is at the door (journal_service refuses an unscheduled
        prepaid debit) and this pass is the detector for anything already on file.
        """
        q = """
            SELECT je.id, je.entry_date, je.entity_id, coalesce(je.source,'manual') AS source,
                   round(l.debit_amount::numeric,2) AS amount,
                   left(coalesce(je.description,''),80) AS descr
            FROM finance_journal_lines l
            JOIN finance_journal_entries je ON je.id = l.entry_id
                 AND je.status IN ('POSTED','DRAFT')
            WHERE l.account_code = :prepaid AND l.debit_amount > 0
              AND je.entry_date <= :as_of
              AND coalesce(je.source,'') != 'prepaid_release'
              AND NOT EXISTS (
                SELECT 1 FROM finance_amortization_schedules s
                JOIN finance_invoices i ON i.id = s.invoice_id
                WHERE i.journal_entry_id = je.id)
        """
        params: dict = {"prepaid": PREPAID_ACCOUNT_CODE, "as_of": as_of_date}
        if entity_ids:
            q += " AND je.entity_id = ANY(:ents)"
            params["ents"] = entity_ids
        rows = db.execute(text(q + " ORDER BY l.debit_amount DESC"), params).mappings().all()
        return [{"journal_entry_id": r["id"], "date": r["entry_date"].isoformat(),
                 "entity_id": r["entity_id"], "source": r["source"],
                 "amount": float(r["amount"]), "description": r["descr"],
                 "problem": "parked in Prepayments with no release schedule — it will never "
                            "reach the P&L. Book it through an invoice with a service period, "
                            "or expense it outright."} for r in rows]

    # ── THE SCHEDULED-POSTINGS ENGINE (DA-13, Gaurav 2026-08-17) ─────────────
    # Sibling of the categorization engine: that one turns BANK TRANSACTIONS into journals,
    # this one turns SCHEDULES into journals. One call runs every pending pass in order,
    # idempotently, posting only months that have ARRIVED. Use at month-lock and in year passes.
    def run_all(self, db: Session, as_of_date: date | None = None,
                entity_ids: list[int] | None = None) -> dict:
        if as_of_date is None:
            as_of_date = date.today()
        registered = self.register_pending(db, as_of_date=as_of_date, entity_ids=entity_ids)
        adjustments = self.apply_asset_adjustments(db, as_of_date=as_of_date)
        assets = self.run(db, as_of_date=as_of_date)
        prepaids = self.run_prepaids(db, as_of_date=as_of_date)
        # The asset side self-heals (a policy supplies the life); the prepaid side cannot invent a
        # service period, so anything already parked without a schedule is REPORTED here and must
        # be resolved by hand. New ones are refused at the door by journal_service (DA-15).
        stranded = self.unscheduled_prepaids(db, as_of_date=as_of_date, entity_ids=entity_ids)
        return {
            "as_of_date": as_of_date.isoformat(),
            "registered": registered,
            "unscheduled_prepaids": stranded,
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
                            or_(FinanceJournalEntry.source.is_(None),
                                FinanceJournalEntry.source.notin_(_SCHEDULED_SOURCES)),
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
    def _is_pl_account(self, db: Session, code: str | None) -> bool:
        """DA-14: only EXPENSE / COST_OF_SALES accounts can receive a prepaid release."""
        if not code:
            return False
        row = db.execute(text(
            "SELECT account_type FROM finance_accounts WHERE code = :c "
            "ORDER BY entity_id NULLS FIRST LIMIT 1"), {"c": code}).first()
        if row is None:
            return False
        return str(row[0]).upper().endswith(("EXPENSE", "COST_OF_SALES"))

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
                # DA-14 (Gaurav 2026-08-17): a release must land in the P&L. Releasing into an
                # asset or a liability moves money sideways and never becomes an expense — that
                # spend is capitalized and belongs to the asset register, not to a spread. The
                # invoice gate now prevents these, and this refuses the ones already on file.
                if not self._is_pl_account(db, sc.expense_account_code):
                    errors.append({"schedule_id": sc.id,
                                   "error": f"release account {sc.expense_account_code} is not a "
                                            f"P&L account — capitalized spend cannot be spread "
                                            f"(DA-14); this schedule needs re-coding or cancelling"})
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
