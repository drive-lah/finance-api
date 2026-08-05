"""Transaction service for business logic."""

from typing import Dict, Any, List, Optional
from datetime import datetime, date
from decimal import Decimal
import json
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from src.models.transaction import FinanceTransaction, TransactionStatus
from src.models.journal_entry import FinanceJournalEntry, JournalEntryStatus
from src.models.bank_account import FinanceBankAccount
from src.models.counterparty import FinanceCounterparty
from src.services.csv_adapters import get_adapter
from src.utils.fingerprint import generate_fingerprint


class TransactionService:
    """Service layer for transaction operations."""

    def get_all(
        self,
        db: Session,
        bank_account_id: Optional[int] = None,
        entity_id: Optional[int] = None,
        counterparty_id: Optional[int] = None,
        status: Optional[TransactionStatus] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        search: Optional[str] = None,
        journal_entry_id: Optional[int] = None,
        amount_min: Optional[float] = None,
        amount_max: Optional[float] = None,
        sort_by: str = "date",
        sort_dir: str = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> List[FinanceTransaction]:
        """Get transactions with optional filters."""
        from sqlalchemy import func
        query = db.query(FinanceTransaction)

        # Amount filter is on MAGNITUDE (abs) so a range catches both in/out flows.
        if amount_min is not None:
            query = query.filter(func.abs(FinanceTransaction.amount) >= amount_min)
        if amount_max is not None:
            query = query.filter(func.abs(FinanceTransaction.amount) <= amount_max)

        if bank_account_id is not None:
            query = query.filter(FinanceTransaction.bank_account_id == bank_account_id)

        if counterparty_id is not None:
            query = query.filter(FinanceTransaction.counterparty_id == counterparty_id)

        # All legs of one journal entry — how the FE shows "paired with" for transfers
        if journal_entry_id is not None:
            query = query.filter(FinanceTransaction.reconciled_journal_entry_id == journal_entry_id)

        if entity_id is not None:
            bank_account_ids = (
                db.query(FinanceBankAccount.id)
                .filter(FinanceBankAccount.entity_id == entity_id)
                .subquery()
            )
            query = query.filter(FinanceTransaction.bank_account_id.in_(bank_account_ids))

        if status is not None:
            query = query.filter(FinanceTransaction.status == status)

        if date_from is not None:
            query = query.filter(FinanceTransaction.transaction_date >= date_from)

        if date_to is not None:
            query = query.filter(FinanceTransaction.transaction_date <= date_to)

        if search:
            term = f"%{search}%"
            query = query.filter(
                FinanceTransaction.description.ilike(term)
                | FinanceTransaction.counterparty_name.ilike(term)
                | FinanceTransaction.reference_number.ilike(term)
            )

        # id tiebreak: offset paging must never skip/duplicate rows on ties
        asc = sort_dir == "asc"
        if sort_by == "amount":
            primary = FinanceTransaction.amount.asc() if asc else FinanceTransaction.amount.desc()
        else:
            primary = FinanceTransaction.transaction_date.asc() if asc else FinanceTransaction.transaction_date.desc()
        tiebreak = FinanceTransaction.id.asc() if asc else FinanceTransaction.id.desc()
        return (
            query.order_by(primary, tiebreak)
            .limit(limit)
            .offset(offset)
            .all()
        )

    def count_all(
        self,
        db: Session,
        bank_account_id: Optional[int] = None,
        entity_id: Optional[int] = None,
        counterparty_id: Optional[int] = None,
        status: Optional[TransactionStatus] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        search: Optional[str] = None,
        journal_entry_id: Optional[int] = None,
        amount_min: Optional[float] = None,
        amount_max: Optional[float] = None,
    ) -> int:
        """Total rows matching the same filters as get_all (for the pagination header)."""
        from sqlalchemy import func
        query = db.query(FinanceTransaction)
        if amount_min is not None:
            query = query.filter(func.abs(FinanceTransaction.amount) >= amount_min)
        if amount_max is not None:
            query = query.filter(func.abs(FinanceTransaction.amount) <= amount_max)
        if bank_account_id is not None:
            query = query.filter(FinanceTransaction.bank_account_id == bank_account_id)
        if counterparty_id is not None:
            query = query.filter(FinanceTransaction.counterparty_id == counterparty_id)
        if journal_entry_id is not None:
            query = query.filter(FinanceTransaction.reconciled_journal_entry_id == journal_entry_id)
        if entity_id is not None:
            bank_account_ids = (
                db.query(FinanceBankAccount.id)
                .filter(FinanceBankAccount.entity_id == entity_id)
                .subquery()
            )
            query = query.filter(FinanceTransaction.bank_account_id.in_(bank_account_ids))
        if status is not None:
            query = query.filter(FinanceTransaction.status == status)
        if date_from is not None:
            query = query.filter(FinanceTransaction.transaction_date >= date_from)
        if date_to is not None:
            query = query.filter(FinanceTransaction.transaction_date <= date_to)
        if search:
            term = f"%{search}%"
            query = query.filter(
                FinanceTransaction.description.ilike(term)
                | FinanceTransaction.counterparty_name.ilike(term)
                | FinanceTransaction.reference_number.ilike(term)
            )
        return query.count()

    def get_by_id(self, db: Session, transaction_id: int) -> Optional[FinanceTransaction]:
        """Get transaction by ID."""
        return db.query(FinanceTransaction).filter(FinanceTransaction.id == transaction_id).first()

    def approve(self, db: Session, transaction_id: int) -> FinanceTransaction:
        """
        Approve a Matched transaction.

        Posts the linked draft journal entry and sets transaction status to Reconciled.
        Raises ValueError if transaction not found, not in Matched status, or JE already posted.
        """
        transaction = self.get_by_id(db, transaction_id)
        if not transaction:
            raise ValueError(f"Transaction {transaction_id} not found")
        if transaction.status != TransactionStatus.MATCHED:
            raise ValueError(
                f"Transaction must be in Matched status to approve (current: {transaction.status.value})"
            )
        if not transaction.reconciled_journal_entry_id:
            raise ValueError("Transaction has no linked journal entry to approve")

        je = db.get(FinanceJournalEntry, transaction.reconciled_journal_entry_id)
        if not je:
            raise ValueError("Linked journal entry not found")

        # A transfer pair shares ONE journal entry: posting happens once, on the
        # first leg approved. The second leg is a quiet completion, not an error
        # ("Journal entry is already posted" broke bulk approve for every pair,
        # Gaurav 2026-07-26). Approving the first leg also reconciles the other
        # MATCHED legs of the same JE — one JE, one approval.
        now = datetime.utcnow()
        if je.status != JournalEntryStatus.POSTED:
            je.status = JournalEntryStatus.POSTED
            je.posted_at = now
            je.posting_user_id = "admin"
            partners = (
                db.query(FinanceTransaction)
                .filter(
                    FinanceTransaction.reconciled_journal_entry_id == je.id,
                    FinanceTransaction.id != transaction.id,
                    FinanceTransaction.status == TransactionStatus.MATCHED,
                )
                .all()
            )
            for p in partners:
                p.status = TransactionStatus.RECONCILED
                p.reconciled_at = now

        transaction.status = TransactionStatus.RECONCILED
        transaction.reconciled_at = now

        # Self-improving aliases: if the transaction's raw bank description differs
        # from the canonical counterparty name, add it as an alias so future
        # transactions with the same description match at L1 instead of L2/L3.
        self._maybe_add_alias(db, transaction)

        db.commit()
        db.refresh(transaction)

        # COA-policy depreciation/amortization: check if the JE hits an asset account
        try:
            from src.services.amortization_service import amortization_service
            amortization_service.check_and_create_schedule(db, transaction, je)
            db.commit()
        except Exception as e:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                f"Depreciation schedule check failed for transaction {transaction_id}: {e}",
                exc_info=True,
            )

        return transaction

    def _maybe_add_alias(self, db: Session, transaction: FinanceTransaction) -> None:
        """
        Add the transaction's raw description as a counterparty alias if it is
        not already covered by the canonical name or existing aliases.

        Called on approval so that next time an identical bank description arrives
        it resolves at L1 (deterministic) rather than L2/L3.
        """
        if not transaction.counterparty_id:
            return

        cp = db.get(FinanceCounterparty, transaction.counterparty_id)
        if not cp:
            return

        # Build the candidate string: prefer counterparty_name if set (it may be
        # the raw bank string before it was overwritten by the canonical name),
        # otherwise fall back to description.
        raw = (transaction.description or "").strip()
        if not raw:
            return

        canonical = cp.name.lower().strip()
        # Skip if the raw text already matches the canonical name exactly
        if raw.lower() == canonical:
            return

        existing_aliases = [a.lower().strip() for a in (cp.aliases or []) if a]
        if raw.lower() in existing_aliases:
            return  # already known

        # Add the raw description as a new alias
        new_aliases = list(cp.aliases or []) + [raw]
        cp.aliases = new_aliases

        import logging
        logging.getLogger(__name__).info(
            f"Added alias '{raw}' to counterparty '{cp.name}' (id={cp.id})"
        )

    def reject(self, db: Session, transaction_id: int) -> FinanceTransaction:
        """
        Reject a Matched transaction.

        Voids the linked draft journal entry and resets transaction to Pending
        so the categorization engine can re-evaluate it with updated rules.
        Raises ValueError if transaction not found or not in Matched status.
        """
        transaction = self.get_by_id(db, transaction_id)
        if not transaction:
            raise ValueError(f"Transaction {transaction_id} not found")
        if transaction.status != TransactionStatus.MATCHED:
            raise ValueError(
                f"Transaction must be in Matched status to reject (current: {transaction.status.value})"
            )

        je_voided = False
        je_id = transaction.reconciled_journal_entry_id
        if je_id:
            je = db.get(FinanceJournalEntry, je_id)
            if je and je.status == JournalEntryStatus.DRAFT:
                je.status = JournalEntryStatus.VOID
                je_voided = True

        def _reset(t: FinanceTransaction) -> None:
            t.status = TransactionStatus.PENDING
            t.reconciled_journal_entry_id = None
            t.reconciled_at = None
            t.categorized_by_rule_id = None
            t.categorized_by_logic = None
            t.coa_account_code = None
            t.categorization_type = None
            t.expected_counterpart_ba_id = None

        _reset(transaction)

        # CASCADE (Gaurav, 2026-07-26): a transfer pair shares ONE journal entry.
        # Voiding it must reset BOTH legs — otherwise the partner stays "Matched"
        # pointing at a VOID JE. Only MATCHED partners cascade; a RECONCILED
        # partner means a human approved a POSTED JE, which reject never touches.
        if je_voided:
            partners = (
                db.query(FinanceTransaction)
                .filter(
                    FinanceTransaction.reconciled_journal_entry_id == je_id,
                    FinanceTransaction.id != transaction.id,
                    FinanceTransaction.status == TransactionStatus.MATCHED,
                )
                .all()
            )
            for p in partners:
                _reset(p)

        db.commit()
        db.refresh(transaction)
        return transaction

    def validate_bank_account_exists(self, db: Session, bank_account_id: int) -> bool:
        """Check if bank account exists."""
        return db.query(FinanceBankAccount).filter(FinanceBankAccount.id == bank_account_id).first() is not None

    def import_file(
        self,
        db: Session,
        bank_account_id: int,
        file_bytes: bytes,
        import_batch_id: Optional[str] = None,
        auto_categorize: bool = False,  # Gaurav 2026-07-25: imports ALWAYS stage as IMPORTED; categorization is an explicit act
    ) -> Dict[str, Any]:
        """
        Import transactions from a file (CSV or PDF) using a bank-specific adapter.

        The adapter is selected from the bank account's file_adapter field.
        Smart adapters (e.g. CBAAdapter) auto-detect CSV vs PDF from content.

        Args:
            db: Database session
            bank_account_id: ID of the bank account
            file_bytes: Raw file bytes (CSV or PDF)
            import_batch_id: Optional batch ID for grouping imports

        Returns:
            Dict with import summary: transactions_created, duplicates_skipped, errors

        Raises:
            ValueError: If bank account not found or no adapter exists for the bank.
        """
        bank_account = db.get(FinanceBankAccount, bank_account_id)
        if not bank_account:
            raise ValueError(f"Bank account with id {bank_account_id} not found")

        if not bank_account.file_adapter:
            raise ValueError(
                f"Bank account {bank_account_id} has no file_adapter set. "
                f"Update the bank account with a file_adapter value before importing."
            )
        adapter = get_adapter(bank_account.file_adapter)

        if import_batch_id is None:
            import_batch_id = datetime.utcnow().strftime('%Y%m%d%H%M%S')

        from src.models.sync_run import start_run, finish_run
        run = start_run(db, "file_import", entity_id=bank_account.entity_id,
                        bank_account_id=bank_account.id)
        try:
            normalized_rows = adapter.parse(file_bytes)
            parse_errors = list(adapter.errors)
        except Exception as parse_err:
            finish_run(db, run, error=parse_err)
            raise

        # WRONG-ACCOUNT GUARD (Gaurav, 2026-07-25): when the statement declares
        # its own account number, it must match the selected bank account.
        stmt_acct = getattr(adapter, "statement_account_number", "") or ""
        if stmt_acct:
            ours = "".join(ch for ch in (bank_account.account_number or "") if ch.isdigit())
            theirs = "".join(ch for ch in stmt_acct if ch.isdigit())
            if ours and theirs and ours != theirs:
                raise ValueError(
                    f"Statement belongs to account {stmt_acct}, but you selected "
                    f"'{bank_account.account_name}' ({bank_account.account_number}). "
                    f"Upload it to the matching bank account.")

        try:
            result = self.import_from_rows(
                db=db,
                bank_account=bank_account,
                normalized_rows=normalized_rows,
                fingerprint_fn=adapter.fingerprint_fields,
                import_batch_id=import_batch_id,
                source="file_import",
                extra_errors=parse_errors,
                auto_categorize=auto_categorize,
            )
        except Exception as import_err:
            finish_run(db, run, fetched=len(normalized_rows), error=import_err)
            raise
        finish_run(db, run, fetched=len(normalized_rows),
                   created=result.get("transactions_created"),
                   duplicates=result.get("duplicates_skipped"),
                   error="; ".join(str(e) for e in (result.get("errors") or [])[:5]) or None)
        result["statement_opening_balance"] = str(getattr(adapter, "statement_opening_balance", "") or "")
        result["statement_closing_balance"] = str(getattr(adapter, "statement_closing_balance", "") or "")
        return result

    def import_dbs_statement(self, db: Session, entity_id: int, pdf_bytes: bytes) -> Dict[str, Any]:
        """One DBS multi-currency PDF → per-currency fan-out to the entity's
        DBS accounts (SGD/USD/EUR...). Used by BOTH the dedicated /dbs/import
        route AND the generic per-account import when a DBS account is selected
        (Gaurav 2026-07-27: it must not matter WHICH DBS account you pick).
        """
        dbs_adapter = get_adapter("dbs")
        sections = dbs_adapter.parse_pdf(pdf_bytes)   # raises ValueError on parse failure
        results: dict = {}
        total_created = total_dupes = 0
        all_errors: list = list(dbs_adapter.errors)
        section_balances = getattr(dbs_adapter, "section_balances", {}) or {}
        period_end = getattr(dbs_adapter, "statement_period_end", None)
        for currency, rows in sections.items():
            bank_account = (
                db.query(FinanceBankAccount)
                .filter(FinanceBankAccount.entity_id == entity_id,
                        FinanceBankAccount.bank_name.ilike('dbs'),
                        FinanceBankAccount.currency == currency)
                .first())
            if not bank_account:
                results[currency] = {"skipped": f"No DBS {currency} bank account for this entity — create one first."}
                continue

            # Stamp the statement's own carried-forward balance (exists even
            # for ZERO-transaction sections — a dormant currency's standing
            # balance must still show; Gaurav 2026-07-27). Same api_sync_state
            # mechanism as Stripe's provider balance. Never regress the as-of:
            # out-of-order uploads keep the newest statement's stamp.
            cf = (section_balances.get(currency) or {}).get("carried_forward")
            if cf is not None and period_end is not None:
                state = dict(bank_account.api_sync_state or {})
                prior = state.get("balance_as_of")
                if not prior or str(period_end) >= str(prior)[:10]:
                    state["latest_balance"] = str(cf)
                    state["balance_as_of"] = period_end.isoformat()
                    bank_account.api_sync_state = state
                    db.commit()

            if not rows:
                results[currency] = {"skipped": "No transactions in statement",
                                     "statement_balance": str(cf) if cf is not None else None}
                continue
            from src.models.sync_run import start_run, finish_run
            run = start_run(db, "file_import", entity_id=entity_id, bank_account_id=bank_account.id)
            try:
                r = self.import_from_rows(
                    db=db, bank_account=bank_account, normalized_rows=rows,
                    fingerprint_fn=dbs_adapter.fingerprint_fields, source="dbs_pdf_import")
            except Exception as e:
                finish_run(db, run, error=e)
                raise
            finish_run(db, run, fetched=len(rows), created=r.get("transactions_created"),
                       duplicates=r.get("duplicates_skipped"))
            r["bank_account_id"] = bank_account.id
            results[currency] = r
            total_created += r.get("transactions_created", 0)
            total_dupes += r.get("duplicates_skipped", 0)
        return {
            "currencies_found": list(sections.keys()),
            "results": results,
            "parse_warnings": list(dbs_adapter.errors),
            # generic-import-shaped summary so the normal upload dialog renders
            "transactions_created": total_created,
            "duplicates_skipped": total_dupes,
            "errors": all_errors,
        }

    def import_from_rows(
        self,
        db: Session,
        bank_account: FinanceBankAccount,
        normalized_rows: list,
        fingerprint_fn,
        import_batch_id: Optional[str] = None,
        source: str = "api_sync",
        extra_errors: Optional[list] = None,
        auto_categorize: bool = False,  # Gaurav 2026-07-25: imports ALWAYS stage as IMPORTED; categorization is an explicit act
    ) -> Dict[str, Any]:
        """
        Shared import loop used by both CSV and API sync paths.

        fingerprint_fn: callable(NormalizedRow) -> list[str]
            For CSV: adapter.fingerprint_fields
            For API: lambda row: [row.source_id]  (e.g., Wise TransferWise ID)

        If a row has row.source_id set, that is used instead of fingerprint_fn,
        since a platform-assigned ID is the most reliable dedup key.
        """
        if import_batch_id is None:
            import_batch_id = datetime.utcnow().strftime('%Y%m%d%H%M%S')

        bank_account_id = bank_account.id
        errors: list = list(extra_errors or [])
        transactions_created = 0
        duplicates_skipped = 0
        # Track fingerprints added in this batch (session has autoflush=False,
        # so db.query() won't see pending in-session inserts).
        seen_in_batch: set[str] = set()

        for normalized in normalized_rows:
            try:
                # CURRENCY GUARD (Gaurav, 2026-07-27): a row can never land in an
                # account of a different currency — that silently corrupts the
                # ledger and the balance chain (seen: a multi-currency DBS PDF
                # dumped whole into the EUR account via the generic import).
                row_ccy = getattr(normalized, "currency", None)
                if row_ccy and bank_account.currency and row_ccy != bank_account.currency:
                    errors.append({
                        "row": getattr(normalized, "description", "")[:60],
                        "error": f"currency {row_ccy} != account currency {bank_account.currency} — row refused",
                    })
                    continue

                # The adapter/caller's fingerprint_fn is ALWAYS authoritative.
                # (A source_id override lived here on the claim that platform ids
                # are globally unique — Wise reference ids are NOT (related rows
                # share one), which silently dropped 36+ real transactions as
                # duplicates. Callers that want id-only dedup pass [source_id]
                # as their fn — the Stripe payout importer does exactly that.)
                fp_fields = list(fingerprint_fn(normalized))

                fingerprint = generate_fingerprint(
                    bank_account_id=bank_account_id,
                    fields=fp_fields,
                )

                # Check within-batch duplicates first (catches API returning same
                # transaction twice in one statement response).
                if fingerprint in seen_in_batch:
                    duplicates_skipped += 1
                    continue

                existing = db.query(FinanceTransaction).filter(
                    FinanceTransaction.fingerprint == fingerprint
                ).first()
                if existing:
                    duplicates_skipped += 1
                    continue

                seen_in_batch.add(fingerprint)

                transaction = FinanceTransaction(
                    bank_account_id=bank_account_id,
                    transaction_date=normalized.transaction_date,
                    description=(normalized.description or '')[:500],
                    amount=normalized.amount,
                    reference_number=normalized.reference_number,
                    currency=normalized.currency or bank_account.currency,
                    counterparty_name=normalized.counterparty_name,
                    transaction_type=normalized.transaction_type,
                    running_balance=normalized.running_balance,
                    value_date=normalized.value_date,
                    fingerprint=fingerprint,
                    status=(TransactionStatus.PENDING if auto_categorize else TransactionStatus.IMPORTED),
                    source=source,
                    import_batch_id=import_batch_id,
                    original_csv_row=json.dumps(normalized.to_dict(), default=str),
                )
                # Vendor-payout link key: Wise transfer id (strip the "TRANSFER-" prefix the
                # statement carries) so the payout register can be matched deterministically.
                sid = getattr(normalized, "source_id", None)
                if sid:
                    transaction.wise_transfer_id = str(sid).replace("TRANSFER-", "")

                db.add(transaction)
                transactions_created += 1

            except Exception as e:
                errors.append({"error": str(e)})
                continue

        try:
            db.commit()
        except IntegrityError as e:
            db.rollback()
            raise ValueError(f"Database error during import: {str(e)}")

        # Deterministic vendor-payout auto-pair (§7): for each newly-imported outbound row
        # whose transfer id matches an awaiting_import payout, pair the txn to the invoice and
        # post the knock-off. Defensive — never breaks the import if pairing errors.
        try:
            from src.services.payout_service import payout_service
            newly = (db.query(FinanceTransaction)
                     .filter(FinanceTransaction.import_batch_id == import_batch_id,
                             FinanceTransaction.wise_transfer_id.isnot(None)).all())
            paired = 0
            for t in newly:
                try:
                    if payout_service.pair_on_import(db, t):
                        paired += 1
                except Exception as pe:
                    logging.getLogger(__name__).warning(
                        f"payout pair_on_import failed for txn {t.id}: {pe}")
            if paired:
                db.commit()
        except Exception as e:
            logging.getLogger(__name__).warning(f"payout auto-pair sweep skipped: {e}")

        return {
            "transactions_created": transactions_created,
            "duplicates_skipped": duplicates_skipped,
            "errors": errors,
            "import_batch_id": import_batch_id,
        }

    def create_from_stripe(
        self,
        db: Session,
        bank_account_id: int,
        source_external_id: str,
        transaction_date: date,
        description: str,
        amount: Decimal,
        reference_number: Optional[str] = None
    ) -> FinanceTransaction:
        """
        Create a transaction from Stripe webhook data.

        Args:
            db: Database session
            bank_account_id: ID of the bank account
            source_external_id: External source transaction ID (Stripe, Wise, etc.)
            transaction_date: Date of the transaction
            description: Transaction description
            amount: Transaction amount
            reference_number: Optional reference number

        Returns:
            Created transaction

        Raises:
            ValueError: If bank account doesn't exist or duplicate source transaction ID
        """
        # Validate bank account exists
        if not self.validate_bank_account_exists(db, bank_account_id):
            raise ValueError(f"Bank account with id {bank_account_id} not found")

        # Check for duplicate Stripe transaction ID (source_external_id)
        existing_stripe = db.query(FinanceTransaction).filter(
            FinanceTransaction.source_external_id == source_external_id
        ).first()

        if existing_stripe:
            raise ValueError(f"Transaction with Stripe ID {source_external_id} already exists")
        
        # Generate fingerprint for Stripe transactions.
        # Stripe has source_external_id as the primary dedup key,
        # but we also fingerprint on date + amount + reference as a
        # secondary check. No running_balance for Stripe.
        fingerprint = generate_fingerprint(
            bank_account_id=bank_account_id,
            fields=[
                transaction_date.isoformat(),
                f"{amount:.2f}",
                (reference_number or "").strip().lower(),
            ],
        )

        # Check for duplicate fingerprint
        existing_fingerprint = db.query(FinanceTransaction).filter(
            FinanceTransaction.fingerprint == fingerprint
        ).first()
        
        if existing_fingerprint:
            raise ValueError(f"Transaction with same fingerprint already exists (duplicate transaction)")
        
        # Create transaction
        transaction = FinanceTransaction(
            bank_account_id=bank_account_id,
            transaction_date=transaction_date,
            description=description,
            amount=amount,
            reference_number=reference_number,
            fingerprint=fingerprint,
            status=TransactionStatus.PENDING,
            source='stripe_automation',
            source_external_id=source_external_id
        )
        
        db.add(transaction)
        db.commit()
        db.refresh(transaction)

        return transaction

    def resolve_needs_review(
        self,
        db: Session,
        transaction_id: int,
        account_code: str,
        counterparty_id: Optional[int] = None,
        resolved_by: Optional[str] = None,
        add_alias: Optional[str] = None,
    ) -> FinanceTransaction:
        """
        Resolve a NEEDS_REVIEW transaction by accepting or overriding the AI suggestion.

        Creates a journal entry using account_code and transitions the transaction
        to MATCHED status. The human-provided account_code can confirm the AI
        suggestion (ai_suggested_account_code) or override it with a different code.

        If add_alias is provided, it is appended to the counterparty's alias list
        so future transactions with the same description auto-match at L1.

        Raises ValueError if transaction not found or not in NEEDS_REVIEW status.
        """
        from src.services.journal_service import journal_service
        from src.models.bank_account import FinanceBankAccount

        transaction = self.get_by_id(db, transaction_id)
        if not transaction:
            raise ValueError(f"Transaction {transaction_id} not found")
        if transaction.status != TransactionStatus.NEEDS_REVIEW:
            raise ValueError(
                f"Transaction must be in Needs Review status to resolve "
                f"(current: {transaction.status.value})"
            )

        bank_account = db.get(FinanceBankAccount, transaction.bank_account_id)
        if not bank_account or not bank_account.coa_account_code:
            raise ValueError(
                f"Bank account for transaction {transaction_id} has no COA code set."
            )

        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        abs_amount = abs(amount)

        # POL-25: book in the entity's functional currency, converted at the
        # monthly standard rate (POL-26); native amount + rate stamped per line.
        from decimal import Decimal
        from src.models.entity import FinanceEntity
        from src.services.fx_service import fx_service
        entity_row = db.get(FinanceEntity, bank_account.entity_id)
        functional_ccy = entity_row.base_currency if entity_row else None
        native_ccy = transaction.currency or functional_ccy
        native_abs = Decimal(str(abs_amount))
        fx_rate = Decimal("1")
        if functional_ccy and native_ccy != functional_ccy:
            functional_abs, fx_rate = fx_service.to_functional(
                db, native_abs, native_ccy, functional_ccy,
                transaction.transaction_date)
            abs_amount = float(functional_abs)

        # Build JE lines: outgoing (negative) → Dr account / Cr bank
        #                 incoming (positive) → Dr bank / Cr account
        if amount < 0:
            lines = [
                {"account_code": account_code,                     "debit_amount": abs_amount, "credit_amount": 0.0,       "description": transaction.description},
                {"account_code": bank_account.coa_account_code,   "debit_amount": 0.0,        "credit_amount": abs_amount, "description": transaction.description},
            ]
        else:
            lines = [
                {"account_code": bank_account.coa_account_code,   "debit_amount": abs_amount, "credit_amount": 0.0,       "description": transaction.description},
                {"account_code": account_code,                    "debit_amount": 0.0,        "credit_amount": abs_amount, "description": transaction.description},
            ]
        for l in lines:
            l["currency"] = native_ccy
            l["fx_rate"] = fx_rate
            l["native_amount"] = native_abs

        je = journal_service.create(
            db=db,
            entity_id=bank_account.entity_id,
            entry_date=transaction.transaction_date,
            description=f"Resolved (NEEDS_REVIEW): {transaction.description or transaction.id}",
            lines=lines,
            created_by=resolved_by,
        )
        je.source = "needs_review_resolution"
        db.flush()

        if counterparty_id:
            transaction.counterparty_id = counterparty_id

        now = datetime.utcnow()
        transaction.status = TransactionStatus.MATCHED
        transaction.reconciled_journal_entry_id = je.id
        transaction.matched_at = now
        transaction.coa_account_code = account_code
        transaction.categorized_by_logic = 'needs_review_resolution'

        # Alias suggestion: add add_alias string to counterparty's alias list
        if add_alias and transaction.counterparty_id:
            cp = db.get(FinanceCounterparty, transaction.counterparty_id)
            if cp:
                alias_clean = add_alias.strip()
                existing = [a.lower() for a in (cp.aliases or [])]
                if alias_clean.lower() not in existing and alias_clean.lower() != cp.name.lower():
                    cp.aliases = list(cp.aliases or []) + [alias_clean]

        db.commit()
        db.refresh(transaction)
        return transaction


# Singleton instance
transaction_service = TransactionService()
