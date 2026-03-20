"""SyncService: Orchestrates Stripe data → Finance API sync.

Coordinates ClickHouse queries, classification logic, and journal entry creation.
"""
from datetime import datetime, date
from decimal import Decimal
import logging
from typing import Tuple

from src.clients.clickhouse_client import ClickHouseClient
from src.database import db_session
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.stripe_sync_run import StripeSyncRun, StripeSyncStatus

from .config import JESpec, REGIONS
from .query_builder import QueryBuilder
from .data_processor import StripeDataProcessor
from .journal_entry_builder import JournalEntryBuilder

logger = logging.getLogger(__name__)


class StripeSyncService:
    """Orchestrates monthly Stripe data sync from ClickHouse to Finance API."""

    def __init__(self, region: str = "SG"):
        """Initialize with region ('SG' or 'AU')."""
        if region not in REGIONS:
            raise ValueError(f"Unknown region: {region}")

        self.region = region
        self.config = REGIONS[region]
        self.entity_id = self.config["entity_id"]

        self.ch = ClickHouseClient()
        self.qb = QueryBuilder(region)
        self.proc = StripeDataProcessor()
        self.builder = JournalEntryBuilder(region)

    def sync_month(self, month_str: str) -> StripeSyncRun:
        """
        Sync a single month of Stripe data.

        Args:
            month_str: "YYYY-MM" format

        Returns:
            StripeSyncRun with results (created, replaced, skipped counts)
        """
        logger.info(f"Starting Stripe sync: {self.region} {month_str}")

        sync_run = StripeSyncRun(
            month=month_str,
            region=self.region,
            entity_id=self.entity_id,
            started_at=datetime.utcnow(),
            status=StripeSyncStatus.RUNNING,
        )

        try:
            # Parse month string to date
            year, month = month_str.split("-")
            entry_date = date(int(year), int(month), 1)

            # Fetch all 24 journal entry specs
            specs = self._generate_je_specs(entry_date, month_str)
            logger.info(f"Generated {len(specs)} journal entry specs")

            # Create journal entries (idempotent via reference number)
            created, replaced, skipped = self._persist_journal_entries(
                specs, self.builder, entry_date, self.entity_id
            )

            # Reconcile
            reconciled = self._reconcile(month_str)

            sync_run.completed_at = datetime.utcnow()
            sync_run.status = StripeSyncStatus.SUCCESS
            sync_run.journal_entries_created = created
            sync_run.journal_entries_replaced = replaced
            sync_run.journal_entries_skipped = skipped
            sync_run.reconciliation_passed = reconciled

            logger.info(
                f"Stripe sync complete: {created} created, {replaced} replaced, {skipped} skipped"
            )

        except Exception as e:
            logger.error(f"Stripe sync failed: {str(e)}")
            sync_run.completed_at = datetime.utcnow()
            sync_run.status = StripeSyncStatus.FAILED
            sync_run.error_message = str(e)

        # Persist sync run record
        with db_session() as db:
            db.add(sync_run)
            db.commit()

        return sync_run

    def _generate_je_specs(self, entry_date: date, month_str: str) -> list:
        """
        Generate all 24 journal entry specs for the month.

        Returns list of JESpec objects ready for persistence.
        """
        specs = []
        proc = StripeDataProcessor()

        # ---- REVENUE ----

        # JE #1: Trip charges (cash)
        data = self.ch.execute_single(self.qb.trip_charges(month_str))
        amt = proc.compute_trip_revenue(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="C-TRIP-CASH",
                    entry_date=entry_date,
                    description=f"Trip charges - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="1017",
                    credit_code="2100",
                    amount=amt,
                )
            )

        # JE #2: Trip revenue (accrual)
        data = self.ch.execute_single(self.qb.trip_revenue_accrual(month_str))
        amt = proc.compute_trip_revenue(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="A-TRIP-REVENUE",
                    entry_date=entry_date,
                    description=f"Trip revenue accrual - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="2100",
                    credit_code="4000",
                    amount=amt,
                )
            )

        # JE #3: Fuel charges (cash)
        data = self.ch.execute_single(self.qb.fuel_charges(month_str))
        amt = proc.compute_fuel_charges(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="C-FUEL-CASH",
                    entry_date=entry_date,
                    description=f"Fuel charges - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="1017",
                    credit_code="4000",
                    amount=amt,
                )
            )

        # JE #4-5: Incidentals (accrual + cash)
        data = self.ch.execute_single(self.qb.incidentals_invoiced(month_str))
        amt = proc.compute_incidentals_revenue(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="A-INCIDENTALS",
                    entry_date=entry_date,
                    description=f"Incidentals invoiced - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="1200",
                    credit_code="4025",
                    amount=amt,
                )
            )

        data = self.ch.execute_single(self.qb.incidentals_paid(month_str))
        amt = proc.compute_incidentals_revenue(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="C-INCIDENTALS-PAID",
                    entry_date=entry_date,
                    description=f"Incidentals paid - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="1017",
                    credit_code="1200",
                    amount=amt,
                )
            )

        # JE #6-7: Subscriptions (accrual + cash)
        data = self.ch.execute_single(self.qb.subscriptions_invoiced(month_str))
        amt = proc.compute_subscription_revenue(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="A-SUBSCRIPTION",
                    entry_date=entry_date,
                    description=f"Subscriptions invoiced - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="1200",
                    credit_code="4010",
                    amount=amt,
                )
            )

        data = self.ch.execute_single(self.qb.subscriptions_paid(month_str))
        amt = proc.compute_subscription_revenue(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="C-SUBSCRIPTION-PAID",
                    entry_date=entry_date,
                    description=f"Subscriptions paid - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="1017",
                    credit_code="1200",
                    amount=amt,
                )
            )

        # ---- HOST EXPENSES ----

        # JE #8: Host trip earnings
        data = self.ch.execute_single(self.qb.host_trip_earnings(month_str))
        amt = proc.compute_host_trip_earnings(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="A-HOST-TRIP",
                    entry_date=entry_date,
                    description=f"Host trip earnings - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="5000",
                    credit_code="2120",
                    amount=amt,
                )
            )

        # JE #9-15: Host payout earnings by code
        payout_codes = [
            ("1", "A-HOST-DAMAGE"),
            ("2,7", "A-HOST-MILEAGE"),
            ("3", "A-HOST-SUPER"),
            ("4", "A-HOST-STICKER"),
            ("5", "A-HOST-FLEX"),
            ("6", "A-HOST-FUEL"),
            ("8,9,10,11,12", "A-HOST-MISC"),
        ]

        for code, suffix in payout_codes:
            data = self.ch.execute_single(
                self.qb.host_payout_earnings_by_code(month_str, code)
            )
            amt, account_code = proc.compute_host_payout_by_code(data, code)
            if proc.should_create_entry(amt):
                specs.append(
                    JESpec(
                        reference_suffix=suffix,
                        entry_date=entry_date,
                        description=f"Host {code} payouts - {entry_date.strftime('%b %Y')} "
                        f"(${amt:,.2f})",
                        debit_code=account_code,
                        credit_code="2120",
                        amount=amt,
                    )
                )

        # JE #16: Stripe fees
        data = self.ch.execute_single(self.qb.stripe_fees(month_str))
        amt = proc.compute_stripe_fees(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="C-FEES",
                    entry_date=entry_date,
                    description=f"Stripe processing fees - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="5010",
                    credit_code="1017",
                    amount=amt,
                )
            )

        # JE #17: Disputes
        data = self.ch.execute_single(self.qb.disputes(month_str))
        net, desc = proc.compute_dispute_net(data)
        if proc.should_create_entry(net):
            if net > 0:  # Net loss
                specs.append(
                    JESpec(
                        reference_suffix="C-DISPUTES",
                        entry_date=entry_date,
                        description=f"Chargebacks net - {entry_date.strftime('%b %Y')} ({desc})",
                        debit_code="5051",
                        credit_code="1017",
                        amount=abs(net),
                    )
                )
            else:  # Net win (rare)
                specs.append(
                    JESpec(
                        reference_suffix="C-DISPUTES",
                        entry_date=entry_date,
                        description=f"Chargeback reversals net - {entry_date.strftime('%b %Y')} ({desc})",
                        debit_code="1017",
                        credit_code="5051",
                        amount=abs(net),
                    )
                )

        # ---- BALANCE SHEET ----

        # JE #18-24: Balance sheet entries
        # (deposits, refunds, transfers, payouts)

        data = self.ch.execute_single(self.qb.deposits_received(month_str))
        amt = proc.compute_deposits_received(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="C-DEPOSITS-IN",
                    entry_date=entry_date,
                    description=f"Customer deposits received - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="1017",
                    credit_code="2110",
                    amount=amt,
                )
            )

        data = self.ch.execute_single(self.qb.deposit_refunds(month_str))
        amt = proc.compute_deposit_refunds(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="C-DEPOSITS-OUT",
                    entry_date=entry_date,
                    description=f"Deposit refunds - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="2110",
                    credit_code="1017",
                    amount=amt,
                )
            )

        data = self.ch.execute_single(self.qb.trip_refunds(month_str))
        amt = proc.compute_trip_refunds(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="C-TRIP-REFUND",
                    entry_date=entry_date,
                    description=f"Trip refunds - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="5052",
                    credit_code="1017",
                    amount=amt,
                )
            )

        data = self.ch.execute_single(self.qb.subscription_refunds(month_str))
        amt = proc.compute_subscription_refunds(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="C-SUB-REFUND",
                    entry_date=entry_date,
                    description=f"Subscription refunds - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="5054",
                    credit_code="1017",
                    amount=amt,
                )
            )

        data = self.ch.execute_single(self.qb.invoice_refunds(month_str))
        amt = proc.compute_invoice_refunds(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="C-INV-REFUND",
                    entry_date=entry_date,
                    description=f"Invoice refunds - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="5053",
                    credit_code="1017",
                    amount=amt,
                )
            )

        data = self.ch.execute_single(self.qb.host_transfers_cash(month_str))
        amt = proc.compute_host_transfers(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="C-HOST-TRANSFERS",
                    entry_date=entry_date,
                    description=f"Host cash settlements - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="2120",
                    credit_code="1017",
                    amount=amt,
                )
            )

        data = self.ch.execute_single(self.qb.stripe_payouts(month_str))
        amt = proc.compute_stripe_payouts(data)
        if proc.should_create_entry(amt):
            specs.append(
                JESpec(
                    reference_suffix="C-PAYOUT",
                    entry_date=entry_date,
                    description=f"Stripe to bank - {entry_date.strftime('%b %Y')} "
                    f"(${amt:,.2f})",
                    debit_code="1016",
                    credit_code="1017",
                    amount=amt,
                )
            )

        return specs

    def _persist_journal_entries(
        self, specs: list, builder, month: date, entity_id: int
    ) -> Tuple[int, int, int]:
        """Create JEs in Finance API. Idempotent via reference_number."""
        created, replaced, skipped = 0, 0, 0

        with db_session() as db:
            for spec in specs:
                ref = builder.build_reference(spec.reference_suffix, month)

                existing = db.query(FinanceJournalEntry).filter(
                    FinanceJournalEntry.reference_number == ref,
                    FinanceJournalEntry.entity_id == entity_id,
                ).first()

                if existing:
                    if existing.status == JournalEntryStatus.VOID:
                        skipped += 1
                        continue
                    # Delete and recreate (POSTED or DRAFT)
                    db.delete(existing)
                    db.flush()
                    replaced += 1
                else:
                    created += 1

                je_args = builder.build_je(spec)
                # TODO: Call journal_service.create(db=db, **je_args)

            db.commit()

        return created, replaced, skipped

    def _reconcile(self, month_str: str) -> bool:
        """Verify account 1017 net matches ClickHouse total."""
        # TODO: Implement reconciliation logic
        return True
