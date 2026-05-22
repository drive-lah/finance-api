"""SyncService: Orchestrates Stripe data → Finance API sync.

Coordinates ClickHouse queries, classification logic, and journal entry creation.
"""
from datetime import datetime, date
from decimal import Decimal
from dataclasses import asdict
import logging
import time
from typing import Optional

from src.clients.clickhouse_client import ClickHouseClient
from src.database import db_session
from src.models.journal_entry import FinanceJournalEntry
from src.models.stripe_sync_run import StripeSyncRun, StripeSyncStatus
# Import all models to ensure metadata is loaded for SQLAlchemy relationships
import src.models

from .config import JESpec, REGIONS, PAYOUTTYPE_TO_ACCOUNT
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

    def sync_month(self, month_str: str) -> dict:
        """
        Sync a single month of Stripe data to PostgreSQL.

        Args:
            month_str: "YYYY-MM" format

        Returns:
            {
                "month": "2025-12",
                "region": "SG",
                "status": "success",
                "journal_entries_created": 25,
                "internal_transfers_created": 4,
                "errors": [],
                "execution_time_seconds": 3.45
            }
        """
        import time
        start_time = time.time()
        
        logger.info(f"Starting Stripe monthly sync: {self.region} {month_str}")
        
        try:
            # Parse month string to date
            year, month = month_str.split("-")
            entry_date = date(int(year), int(month), 1)

            # Step 1: Generate all 25 JE specs by calling query_builder methods
            all_specs = self._generate_all_je_specs(entry_date, month_str)
            logger.info(f"Generated {len(all_specs)} journal entry specs")

            # Step 2: Separate into transfer vs non-transfer JEs
            transfer_specs = self._get_transfer_specs(all_specs)
            non_transfer_specs = [s for s in all_specs if s not in transfer_specs]
            
            logger.info(
                f"Categorized: {len(non_transfer_specs)} non-transfers, "
                f"{len(transfer_specs)} transfers"
            )

            # Step 3: Create non-transfer JournalEntry records (direct to ledger)
            je_created = self._create_journal_entries(
                non_transfer_specs, entry_date
            )
            logger.info(f"Created {je_created} journal entries")

            # Step 4: Create transfer FinanceTransaction records (AWAITING_MATCH)
            txn_created = self._create_transfer_transactions(
                transfer_specs, entry_date
            )
            logger.info(f"Created {txn_created} internal transfer transactions")

            # Step 5: Log sync run to audit trail
            self._log_sync_run(month_str, je_created, txn_created, "success", None)

            execution_time = time.time() - start_time
            
            return {
                "month": month_str,
                "region": self.region,
                "status": "success",
                "journal_entries_created": je_created,
                "internal_transfers_created": txn_created,
                "total_amount": sum(Decimal(str(s.amount)) for s in all_specs),
                "errors": [],
                "execution_time_seconds": round(execution_time, 2),
            }

        except Exception as e:
            logger.error(f"Stripe sync failed: {str(e)}", exc_info=True)
            self._log_sync_run(month_str, 0, 0, "failed", str(e))
            
            execution_time = time.time() - start_time
            return {
                "month": month_str,
                "region": self.region,
                "status": "failed",
                "journal_entries_created": 0,
                "internal_transfers_created": 0,
                "errors": [str(e)],
                "execution_time_seconds": round(execution_time, 2),
            }

    def _generate_all_je_specs(self, entry_date: date, month_str: str) -> list:
        """Generate all 25 JE specs by calling query_builder methods."""
        specs = []
        
        # Define all 25 JE methods with their metadata
        # (method_name, debit_code, credit_code, je_number, is_transfer, description_prefix)
        je_methods = [
            # Revenue JEs (1-7)
            ("trip_charges", "1017", "2100", 1, False, "Trip charges (cash)"),
            ("trip_revenue_accrual", "2100", "4000", 2, False, "Trip revenue accrual"),
            ("fuel_charges", "1017", "4000", 3, False, "Fuel auto-charges"),
            ("incidentals_invoiced", "1200", "4025", 4, False, "Incidentals invoiced"),
            ("incidentals_paid", "1017", "1200", 5, False, "Incidentals cash received"),
            ("subscriptions_invoiced", "1200", "4010", 6, False, "Subscriptions invoiced"),
            ("subscriptions_paid", "1017", "1200", 7, False, "Subscriptions cash received"),
            
            # Host Earnings JEs (8-15)
            ("host_trip_earnings", "5000", "2120", 8, False, "Host trip earnings accrual"),
            ("host_damage_payout", "5021", "2120", 9, False, "Host damage payouts"),
            ("host_excess_mileage_payout", "5024", "2120", 10, False, "Host excess mileage"),
            ("host_fuel_payout", "5023", "2120", 11, False, "Host fuel reimbursement"),
            ("host_tolls_payout", "5025", "2120", 12, False, "Host toll reimbursement"),
            ("host_cleanliness_payout", "5022", "2120", 13, False, "Host cleanliness payout"),
            ("host_flexplus_payout", "5002", "2120", 14, False, "Host FlexPlus bonus"),
            ("host_superhost_payout", "5040", "2120", 15, False, "Host superhost bonus"),
            
            # Processing Fees JEs (16-17)
            ("stripe_fees", "5010", "1017", 16, False, "Stripe platform fees"),
            ("disputes", "5051", "1017", 17, False, "Chargeback disputes"),
            
            # Balance Sheet JEs (18-24)
            ("deposits_received", "1017", "2110", 18, False, "Customer deposits received"),
            ("deposit_refunds", "2110", "1017", 19, False, "Deposit refunds"),
            ("trip_refunds", "5052", "1017", 20, False, "Trip refunds"),
            ("subscription_refunds", "5054", "1017", 21, False, "Subscription refunds"),
            ("invoice_refunds", "5053", "1017", 22, False, "Invoice refunds"),
            ("host_transfers_cash", "2120", "1017", 23, False, "Host transfer payouts"),  # DIRECT
            ("stripe_payouts", "1017", "1018", 24, True, "Stripe payouts"),  # TRANSFER
            
            # Direct Revenue JE (25)
            ("incidentals_direct_revenue", "1017", "4021", 25, False, "Direct incidentals (no AR)"),
        ]
        
        for method_name, debit, credit, je_num, is_transfer, description in je_methods:
            try:
                # Call the query builder method
                query = getattr(self.qb, method_name)(month_str)
                if query is None:
                    continue  # JE not applicable for this region (e.g. SG fuel charges)
                result = self.ch.execute_single(query)
                
                if result and result.get("amount"):
                    amount = Decimal(str(result["amount"]))
                    
                    # Only create entry if amount is non-zero
                    if amount != 0:
                        specs.append(
                            JESpec(
                                reference_suffix=f"JE{je_num:02d}-{self.region}",
                                entry_date=entry_date,
                                description=f"JE #{je_num}: {description} - {entry_date.strftime('%b %Y')} (${amount:,.2f})",
                                debit_code=debit,
                                credit_code=credit,
                                amount=amount,
                                je_number=je_num,
                                is_transfer=is_transfer,
                            )
                        )
                        logger.debug(f"JE #{je_num}: {description} = ${amount:,.2f}")
                
            except Exception as e:
                logger.warning(f"Error querying JE #{je_num} ({method_name}): {str(e)}")
                continue
        
        return specs

    def _get_transfer_specs(self, all_specs: list) -> list:
        """Filter out internal transfer JE specs (JE #23-24)."""
        return [s for s in all_specs if hasattr(s, 'is_transfer') and s.is_transfer]

    def _create_journal_entries(self, specs: list, entry_date: date) -> int:
        """
        Create non-transfer JournalEntry records in PostgreSQL.
        
        Returns count of entries created.
        """
        created = 0
        
        with db_session() as db:
            for spec in specs:
                try:
                    # Build JE from spec
                    je_args = self.builder.build_je(spec)
                    
                    # Check for duplicate (idempotent)
                    ref = je_args.reference_number
                    existing = db.query(FinanceJournalEntry).filter(
                        FinanceJournalEntry.reference_number == ref,
                        FinanceJournalEntry.entity_id == self.entity_id,
                    ).first()
                    
                    if existing:
                        logger.debug(f"Skipping duplicate JE: {ref}")
                        continue
                    
                    # Create new JE (don't unpack lines - add them separately)
                    je = FinanceJournalEntry(
                        entity_id=je_args.entity_id,
                        entry_date=je_args.entry_date,
                        description=je_args.description,
                        reference_number=je_args.reference_number,
                        status=je_args.status,
                        source='stripe',
                    )
                    
                    # Add journal lines
                    from src.models.journal_line import FinanceJournalLine
                    for line_data in je_args.lines:
                        line = FinanceJournalLine(
                            account_code=line_data['account_code'],
                            debit_amount=line_data['amount'] if line_data['is_debit'] else Decimal('0'),
                            credit_amount=line_data['amount'] if not line_data['is_debit'] else Decimal('0'),
                            entity_id=je_args.entity_id,
                        )
                        je.lines.append(line)
                    
                    db.add(je)
                    created += 1
                    logger.debug(f"Created JE: {ref} (${spec.amount:,.2f})")
                    
                except Exception as e:
                    logger.error(f"Error creating JE for spec {spec}: {str(e)}")
                    continue
            
            db.commit()
        
        return created

    def _create_transfer_transactions(self, specs: list, entry_date: date) -> int:
        """
        Create internal transfer FinanceTransaction records with AWAITING_MATCH status.
        
        Returns count of transactions created.
        """
        from src.models.transaction import FinanceTransaction, TransactionStatus
        import hashlib
        
        created = 0
        
        # Map transfer JE numbers to bank account IDs and expected counterpart
        # NOTE: Only JE#24 (Stripe Payouts) creates transfer txns awaiting match
        # JE#23 (Host Transfers) now creates direct JEs like all other JE types
        transfer_mapping = {
            24: (19, None, "Stripe Platform payout"),  # JE#24: Stripe Platform → (varies)
        }
        
        with db_session() as db:
            for spec in specs:
                je_num = spec.je_number
                if je_num not in transfer_mapping:
                    continue
                
                try:
                    from_ba_id, to_ba_id, transfer_desc = transfer_mapping[je_num]
                    
                    # Generate fingerprint for duplicate detection
                    fingerprint_str = f"{from_ba_id}|{spec.amount}|{entry_date}|{transfer_desc}"
                    fingerprint = hashlib.sha256(fingerprint_str.encode()).hexdigest()
                    
                    # Check for duplicate (idempotent)
                    existing = db.query(FinanceTransaction).filter(
                        FinanceTransaction.fingerprint == fingerprint,
                    ).first()
                    
                    if existing:
                        logger.debug(f"Skipping duplicate transfer transaction: {fingerprint}")
                        continue
                    
                    # Create FinanceTransaction with AWAITING_MATCH status
                    txn = FinanceTransaction(
                        bank_account_id=from_ba_id,
                        transaction_date=entry_date,
                        currency=self.config.get("currency", "SGD"),
                        description=spec.description,
                        amount=-abs(spec.amount),  # Negative for outgoing transfers
                        status=TransactionStatus.AWAITING_MATCH,
                        expected_counterpart_ba_id=to_ba_id,
                        reference_number=spec.reference_suffix,
                        fingerprint=fingerprint,
                    )
                    db.add(txn)
                    created += 1
                    logger.debug(
                        f"Created transfer transaction JE#{je_num}: "
                        f"BA{from_ba_id} → BA{to_ba_id} (${spec.amount:,.2f})"
                    )
                    
                except Exception as e:
                    logger.error(f"Error creating transfer for JE #{je_num}: {str(e)}")
                    continue
            
            db.commit()
        
        return created

    def _log_sync_run(
        self, month_str: str, je_created: int, txn_created: int, status: str, error_msg: Optional[str]
    ) -> None:
        """Log sync run to audit trail."""
        try:
            sync_run = StripeSyncRun(
                month=month_str,
                region=self.region,
                entity_id=self.entity_id,
                status=status,
                journal_entries_created=je_created,
                error_message=error_msg,
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )
            
            with db_session() as db:
                db.add(sync_run)
                db.commit()
                logger.info(f"Logged sync run: {month_str} {self.region} {status}")
        except Exception as e:
            logger.error(f"Error logging sync run: {str(e)}")
