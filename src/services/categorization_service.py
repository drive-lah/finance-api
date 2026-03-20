"""
Categorization Engine Service

Core engine that automatically converts bank transactions into journal entries
by applying configurable rules. Supports expense, deposit, and internal transfer
categorization (both intra-entity and intercompany).
"""
import json
import re
import uuid
import logging
from datetime import datetime, UTC
from decimal import Decimal
from typing import Optional, Any
from sqlalchemy.orm import Session

from src.models.transaction import FinanceTransaction, TransactionStatus, CategorizationType
from src.models.bank_account import FinanceBankAccount
from src.models.counterparty import FinanceCounterparty
from src.models.categorization_rule import (
    FinanceCategorizationRule,
    RuleStatus,
    TransactionDirection,
    TransactionCategory,
    MatchOperator,
    AmountOperator,
)
from src.models.tag import FinanceTransactionTag
from src.models.account import FinanceAccount
from src.services.journal_service import journal_service


logger = logging.getLogger(__name__)

# GST account codes
GST_INPUT_TAX_CODE = "1350"   # Input Tax (paid on purchases)
GST_OUTPUT_TAX_CODE = "2500"  # Output Tax (collected on sales)


class CategorizationService:
    """
    Core categorization engine.

    Evaluates pending transactions against active rules in priority order.
    First matching rule wins. On match, creates a journal entry and advances
    the transaction to MATCHED status (pending human or AI confirmation).
    """

    def run(
        self,
        db: Session,
        entity_id: Optional[int] = None,
        bank_account_id: Optional[int] = None,
        rule_id: Optional[int] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        Run the four-phase categorization pipeline on pending transactions.

        Phase 1 — Counterparty Enrichment:
            Match each transaction's raw counterparty_name / description against
            the finance_counterparties directory. Sets counterparty_id FK,
            canonical counterparty_name, and counterparty_type.

        Phase 2 — AP Invoice Knock-off:
            Match transaction to open AP invoices. If matched, create payment JE
            and mark handled so Phase 4 does not double-book.

        Phase 3 — Payroll Knock-off:
            Match transaction to unmatched payroll JE lines. If matched, link
            and mark handled so Phase 4 does not double-book.

        Phase 4 — Accounting Classification:
            A) Walk active rules in priority order (most specific first match wins).
            B) If no rule match: use counterparty's default_account_code if available.
            C) If no rule and no default: run AI classification (high confidence → MATCHED,
               low → NEEDS_REVIEW).
            D) Unmatched transactions stay Pending.

        Args:
            db: Database session
            entity_id: Optional — only process transactions belonging to this entity
            bank_account_id: Optional — only process this bank account
            limit: Maximum transactions to process in one run

        Returns:
            Summary: total_processed, categorized, uncategorized, errors, results
        """
        query = db.query(FinanceTransaction).filter(
            FinanceTransaction.status == TransactionStatus.PENDING
        )

        if bank_account_id is not None:
            query = query.filter(FinanceTransaction.bank_account_id == bank_account_id)
        elif rule_id is not None:
            # When running a specific rule, scope transactions to the rule's bank_account_ids
            # so Phase 0/1 don't process (and count) unrelated transactions.
            rule_obj = db.query(FinanceCategorizationRule).filter(
                FinanceCategorizationRule.id == rule_id
            ).first()
            if rule_obj and rule_obj.bank_account_ids:
                try:
                    allowed_ids = json.loads(rule_obj.bank_account_ids)
                    if allowed_ids:
                        query = query.filter(FinanceTransaction.bank_account_id.in_(allowed_ids))
                except (json.JSONDecodeError, TypeError):
                    pass
        elif entity_id is not None:
            bank_account_ids = [
                row[0] for row in db.query(FinanceBankAccount.id).filter(
                    FinanceBankAccount.entity_id == entity_id
                ).all()
            ]
            query = query.filter(FinanceTransaction.bank_account_id.in_(bank_account_ids))

        transactions = query.limit(limit).all()

        results: list[dict[str, Any]] = []
        categorized = 0
        uncategorized = 0
        errors = 0

        # ── Phase 0: Pair AWAITING_MATCH with incoming counter-transactions ─
        # Before enrichment: check if any AWAITING_MATCH transactions are waiting
        # for a bank account in our current scope. Pair them with matching PENDING
        # transactions immediately, bypassing all other phases.
        step0_handled_ids: set[int] = set()
        if transactions:
            in_scope_ba_ids = {t.bank_account_id for t in transactions}
            step0_handled_ids, step0_results = self._pair_awaiting_matches(
                db, in_scope_ba_ids, transactions
            )
            results.extend(step0_results)
            categorized += len(step0_handled_ids)

        # Remove step0-handled transactions from further processing
        transactions = [t for t in transactions if t.id not in step0_handled_ids]

        # ── Phase 1: Counterparty enrichment ──────────────────────────────
        self._enrich_counterparties(db, transactions)

        # ── Phase 2: AP Knock-off ────────────────────────────────────────
        # For any transaction whose counterparty was just enriched, check
        # whether it matches an open AP invoice. If so, create the payment
        # JE (Dr AP / Cr Bank) and mark the transaction handled so Phase 4
        # does not double-book the expense.
        ap_handled_ids: set[int] = self._try_ap_knockoff(db, transactions, results, categorized)
        categorized += len(ap_handled_ids)

        # ── Phase 3: Payroll Knock-off ──────────────────────────────────
        # Check whether any outgoing transaction matches an unmatched line in
        # a posted payroll JE (net salary or CPF payment). If so, link the
        # transaction to the payroll JE instead of running Phase 4 rules.
        payroll_handled_ids: set[int] = self._try_payroll_knockoff(db, transactions, results)
        categorized += len(payroll_handled_ids)

        # ── Phase 4: Accounting classification ───────────────────────────
        rules_query = (
            db.query(FinanceCategorizationRule)
            .filter(FinanceCategorizationRule.status == RuleStatus.ACTIVE)
        )
        if rule_id is not None:
            rules_query = rules_query.filter(FinanceCategorizationRule.id == rule_id)
        rules = rules_query.order_by(FinanceCategorizationRule.priority).all()

        # Pre-load counterparties for Phase 4B default account lookup
        from src.models.counterparty import FinanceCounterparty

        cp_map: dict[int, FinanceCounterparty] = {}

        if transactions:
            cp_ids = {t.counterparty_id for t in transactions if t.counterparty_id}
            if cp_ids:
                cps = db.query(FinanceCounterparty).filter(FinanceCounterparty.id.in_(cp_ids)).all()
                cp_map = {cp.id: cp for cp in cps}

        for transaction in transactions:
            if transaction.id in ap_handled_ids or transaction.id in payroll_handled_ids:
                continue  # already handled by AP or payroll knock-off

            try:
                result = None

                # Phase 4A: Rule-based matching (FIRST — most specific)
                matched_rule = self._match_transaction(transaction, rules, cp_map)
                if matched_rule:
                    result = self._apply_rule(db, transaction, matched_rule)

                # Phase 4B: Default account (SECOND — more generic)
                # NOTE: CASE 3 (amount mismatch with open invoices) is now handled in Phase 1.5B
                # and won't reach Phase 4 (already marked MATCHED and in ap_handled_ids)
                if result is None and transaction.counterparty_id and transaction.counterparty_id in cp_map:
                    cp = cp_map[transaction.counterparty_id]
                    # Use counterparty's default account if available
                    if cp.default_account_code:
                        result = self._apply_default_account(db, transaction, cp)

                if result is not None:
                    results.append(result)
                    categorized += 1
                else:
                    results.append({
                        "transaction_id": transaction.id,
                        "status": "uncategorized",
                        "rule_name": None,
                        "journal_entry_id": None,
                        "error": None,
                    })
                    uncategorized += 1

            except Exception as e:
                logger.error(f"Error categorizing transaction {transaction.id}: {e}", exc_info=True)
                db.rollback()
                results.append({
                    "transaction_id": transaction.id,
                    "status": "error",
                    "rule_name": None,
                    "journal_entry_id": None,
                    "error": str(e),
                })
                errors += 1

        # ── Phase 4: AI classification fallback ───────────────────────────
        # Collect transactions still uncategorized after Phases 1–3.
        # One batched Haiku call; high-confidence → MATCHED, low → NEEDS_REVIEW.
        unhandled = [
            t for t in transactions
            if t.id not in ap_handled_ids
            and t.id not in payroll_handled_ids
            and t.status == TransactionStatus.PENDING
        ]
        if unhandled:
            ai_result_map = self._run_ai_classification(db, unhandled)
            for txn in unhandled:
                ai = ai_result_map.get(txn.id)
                if not ai:
                    continue
                # Replace the "uncategorized" entry in results
                for r in results:
                    if r["transaction_id"] == txn.id:
                        r.update(ai)
                        if ai["status"] == "categorized":
                            categorized += 1
                            uncategorized -= 1
                        elif ai["status"] == "needs_review":
                            # stays in uncategorized count — human must confirm
                            pass
                        break

        return {
            "total_processed": len(transactions),
            "categorized": categorized,
            "uncategorized": uncategorized,
            "errors": errors,
            "results": results,
        }

    # ------------------------------------------------------------------
    # Phase 0: Pair AWAITING_MATCH internal transfers
    # ------------------------------------------------------------------

    def _pair_awaiting_matches(
        self,
        db: Session,
        in_scope_ba_ids: set[int],
        pending_transactions: list[FinanceTransaction],
    ) -> tuple[set[int], list[dict[str, Any]]]:
        """
        Attempt to complete pending AWAITING_MATCH internal transfers.

        Looks for AWAITING_MATCH transactions where expected_counterpart_ba_id is
        one of the bank accounts in the current scope. For each found, tries to
        match a PENDING counter-transaction by amount (±2%) and date (±5 days).

        On match: marks both MATCHED, links both to the existing JE, sets matched_at.

        Returns: (set of pending_transaction IDs handled, list of result dicts)
        """
        from datetime import timedelta

        handled_ids: set[int] = set()
        results: list[dict[str, Any]] = []

        # Find all AWAITING_MATCH transactions waiting for our BAs
        awaiting = db.query(FinanceTransaction).filter(
            FinanceTransaction.status == TransactionStatus.AWAITING_MATCH,
            FinanceTransaction.expected_counterpart_ba_id.in_(in_scope_ba_ids),
        ).all()

        if not awaiting:
            return handled_ids, results

        # Build lookup: pending transactions keyed by bank_account_id
        pending_by_ba: dict[int, list[FinanceTransaction]] = {}
        for txn in pending_transactions:
            pending_by_ba.setdefault(txn.bank_account_id, []).append(txn)

        for waiting_txn in awaiting:
            target_ba_id = waiting_txn.expected_counterpart_ba_id
            candidates = pending_by_ba.get(target_ba_id, [])
            if not candidates:
                continue

            waiting_amount = float(waiting_txn.amount)
            abs_waiting = abs(waiting_amount)

            counter = None
            for candidate in candidates:
                if candidate.id in handled_ids:
                    continue  # already paired in this run
                cand_amount = float(candidate.amount)
                # Amounts should be opposite sign and roughly equal magnitude
                if waiting_amount * cand_amount >= 0:
                    continue  # same sign — not a counter
                if abs_waiting == 0:
                    continue
                if abs(abs(cand_amount) - abs_waiting) / abs_waiting > 0.02:
                    continue  # >2% difference
                date_diff = abs((candidate.transaction_date - waiting_txn.transaction_date).days)
                if date_diff > 5:
                    continue
                counter = candidate
                break

            if not counter:
                continue

            # Pair them
            now = datetime.now(UTC)
            je_id = waiting_txn.reconciled_journal_entry_id

            waiting_txn.status = TransactionStatus.MATCHED
            waiting_txn.matched_at = now
            waiting_txn.expected_counterpart_ba_id = None  # cleared — no longer waiting

            counter.status = TransactionStatus.MATCHED
            counter.reconciled_journal_entry_id = je_id
            counter.matched_at = now

            db.commit()

            handled_ids.add(counter.id)
            results.append({
                "transaction_id": counter.id,
                "status": "categorized",
                "rule_name": f"[step0:paired_with_txn_{waiting_txn.id}]",
                "journal_entry_id": je_id,
                "error": None,
            })
            results.append({
                "transaction_id": waiting_txn.id,
                "status": "categorized",
                "rule_name": f"[step0:paired_with_txn_{counter.id}]",
                "journal_entry_id": je_id,
                "error": None,
            })
            logger.info(
                f"Phase 0: paired txn {waiting_txn.id} (AWAITING_MATCH on ba={waiting_txn.bank_account_id}) "
                f"↔ txn {counter.id} (PENDING on ba={counter.bank_account_id}) via JE {je_id}"
            )

        return handled_ids, results

    def _find_counter_transaction(
        self,
        db: Session,
        txn: FinanceTransaction,
        target_ba_id: int,
    ) -> Optional[FinanceTransaction]:
        """
        Look for a PENDING counter-transaction on target_ba_id that mirrors txn.

        Criteria: opposite sign, amount within ±2%, date within ±5 days.
        Returns the best match or None.
        """
        from datetime import timedelta

        abs_amount = abs(float(txn.amount))
        if abs_amount == 0:
            return None

        date_low = txn.transaction_date - timedelta(days=5)
        date_high = txn.transaction_date + timedelta(days=5)

        candidates = db.query(FinanceTransaction).filter(
            FinanceTransaction.bank_account_id == target_ba_id,
            FinanceTransaction.status == TransactionStatus.PENDING,
            FinanceTransaction.transaction_date.between(date_low, date_high),
        ).all()

        txn_amount = float(txn.amount)
        for candidate in candidates:
            cand_amount = float(candidate.amount)
            if txn_amount * cand_amount >= 0:
                continue  # same sign
            if abs(abs(cand_amount) - abs_amount) / abs_amount > 0.02:
                continue
            return candidate

        return None

    # ------------------------------------------------------------------
    # Phase 2: AP Knock-off
    # ------------------------------------------------------------------

    def _try_ap_knockoff(
        self,
        db: Session,
        transactions: list[FinanceTransaction],
        results: list[dict[str, Any]],
        categorized_counter: int,
    ) -> set[int]:
        """
        Phase 2: AP Knock-off with 3-case matching logic.

        For each enriched transaction (counterparty_id set), check whether it matches
        an open AP invoice using the 3-case framework:

        CASE 1: Reference + Amount + Date all match
          → Create payment JE (Dr AP 2000 / Cr Bank)
          → Use invoice.account_code (INVOICE COA WINS)
          → Status: MATCHED

        CASE 2: Amount + Date match, NO reference (FIFO)
          → Match to oldest invoice
          → Create payment JE, use invoice.account_code
          → Status: MATCHED

        CASE 3: Amount doesn't match any invoice
          → Skip knock-off
          → Return None (Phase 4 will handle via rules or asset account)

        For counterparties with NO open invoices: Skip Phase 2 entirely
          → Let Phase 4 (rules/default) handle instead

        Returns set of transaction IDs that were handled via knock-off.
        """
        from src.services.invoice_service import invoice_service
        from src.models.invoice import FinanceInvoice, InvoiceStatus

        handled: set[int] = set()

        for txn in transactions:
            if not txn.counterparty_id:
                continue
            amount = float(txn.amount) if txn.amount is not None else 0.0
            if amount >= 0:
                continue  # Only outgoing payments knock off AP

            try:
                abs_amount = abs(amount)

                # Check if counterparty has ANY open invoices (Case 3 vs Phase 4 decision point)
                open_statuses = (InvoiceStatus.APPROVED.value, InvoiceStatus.PARTIALLY_PAID.value)
                has_open_invoices = db.query(FinanceInvoice).filter(
                    FinanceInvoice.counterparty_id == txn.counterparty_id,
                    FinanceInvoice.currency == txn.currency,
                    FinanceInvoice.status.in_(open_statuses),
                ).count() > 0

                if not has_open_invoices:
                    # No invoices for this counterparty — skip Phase 2
                    # Let Phase 4 handle (rules, default account, or AI)
                    continue

                # Counterparty has open invoices; try to match using 3-case logic
                invoice = invoice_service.find_matching_invoice(
                    db,
                    txn.counterparty_id,
                    abs_amount,
                    txn.currency,
                    description=txn.description or "",
                    reference_number=txn.reference_number or "",
                    transaction_date=txn.transaction_date,
                )

                if invoice:
                    # CASE 1 or 2: Invoice matched
                    # Use invoice.account_code (INVOICE COA WINS over counterparty default)
                    bank_account = db.query(FinanceBankAccount).filter(
                        FinanceBankAccount.id == txn.bank_account_id
                    ).first()
                    if not bank_account or not bank_account.coa_account_code:
                        continue

                    inv_ref = f"Invoice {invoice.invoice_number or invoice.id}"
                    je = invoice_service.create_ap_payment_entries(
                        db=db,
                        bank_account=bank_account,
                        invoice=invoice,
                        txn_date=txn.transaction_date,
                        abs_amount=abs_amount,
                        source="ap_knockoff",
                        description=f"AP Payment: {inv_ref}",
                    )

                    invoice_service.record_payment(db, invoice.id, abs_amount)

                    now = datetime.now(UTC)
                    txn.status = TransactionStatus.MATCHED
                    txn.reconciled_journal_entry_id = je.id
                    txn.matched_at = now
                    txn.coa_account_code = invoice.account_code  # Store invoice COA
                    txn.categorization_type = CategorizationType.EXPENSE
                    txn.categorized_by_logic = 'invoice_knockoff'
                    db.commit()

                    results.append({
                        "transaction_id": txn.id,
                        "status": "categorized",
                        "rule_name": f"[ap_knockoff:invoice_{invoice.id}]",
                        "journal_entry_id": je.id,
                        "cross_entity": bank_account.entity_id != invoice.entity_id,
                        "error": None,
                    })
                    handled.add(txn.id)
                else:
                    # CASE 3: Amount doesn't match any invoice
                    # Park to 1300 Prepayments asset account (Phase 1.5B)
                    # This defers categorization to vendor-level reconciliation
                    bank_account = db.query(FinanceBankAccount).filter(
                        FinanceBankAccount.id == txn.bank_account_id
                    ).first()
                    if bank_account and bank_account.coa_account_code:
                        entry = self._create_simple_entry(
                            db=db,
                            transaction=txn,
                            entity_id=bank_account.entity_id,
                            bank_coa_code=bank_account.coa_account_code,
                            contra_code="1300",  # Prepayments asset account
                            amount=amount,
                            abs_amount=abs_amount,
                            source="case3_asset_parking",
                        )
                        txn.status = TransactionStatus.MATCHED
                        txn.reconciled_journal_entry_id = entry.id
                        txn.matched_at = datetime.now(UTC)
                        txn.coa_account_code = "1300"
                        # No categorization_type for asset-parked transactions
                        db.commit()

                        results.append({
                            "transaction_id": txn.id,
                            "status": "categorized",
                            "rule_name": "[case3:asset_parking]",
                            "journal_entry_id": entry.id,
                            "error": None,
                        })
                        handled.add(txn.id)  # Stop further processing in Phase 4

            except Exception as e:
                logger.warning(
                    f"AP knock-off check failed for transaction {txn.id}: {e}",
                    exc_info=True,
                )
                db.rollback()
                # Do not add to handled — let Phase 4 handle it normally

        return handled

    # ------------------------------------------------------------------
    # Phase 3: Payroll Knock-off
    # ------------------------------------------------------------------

    def _try_payroll_knockoff(
        self,
        db: Session,
        transactions: list[FinanceTransaction],
        results: list[dict[str, Any]],
    ) -> set[int]:
        """
        Match outgoing bank transactions against open payroll run JEs.

        For each outgoing (negative amount) transaction, check whether there is
        a POSTED payroll run for the same entity where:
          - net_payment_transaction_id is NULL and the amount matches net_amount (±2%)
          - OR cpf_payment_transaction_id is NULL and the amount matches cpf_payable_amount (±2%)
          - run_date is within ±7 days of the transaction date

        On match:
          - Links the transaction to the payroll JE (reconciled_journal_entry_id)
          - Sets transaction status → MATCHED
          - Updates the payroll run's net_ or cpf_payment_transaction_id

        Returns set of transaction IDs handled (skip Phase 4 for these).
        """
        from src.models.payroll import FinancePayrollRun
        from datetime import timedelta

        handled: set[int] = set()

        outgoing = [
            t for t in transactions
            if t.amount is not None and float(t.amount) < 0
        ]
        if not outgoing:
            return handled

        # Resolve entity_id for each bank account in one query
        ba_ids = {t.bank_account_id for t in outgoing}
        ba_entity_map: dict[int, int] = {
            ba.id: ba.entity_id
            for ba in db.query(FinanceBankAccount).filter(
                FinanceBankAccount.id.in_(ba_ids)
            ).all()
        }

        for txn in outgoing:
            entity_id = ba_entity_map.get(txn.bank_account_id)
            if not entity_id:
                continue

            abs_amount = abs(float(txn.amount))
            txn_date = txn.transaction_date
            date_low = txn_date - timedelta(days=7)
            date_high = txn_date + timedelta(days=7)

            try:
                runs = db.query(FinancePayrollRun).filter(
                    FinancePayrollRun.status == "POSTED",
                    FinancePayrollRun.run_date.between(date_low, date_high),
                ).all()

                matched_run = None
                match_type = None

                for run in runs:
                    # Check net salary slot first
                    if run.net_payment_transaction_id is None:
                        net = float(run.net_amount)
                        if net > 0 and abs(abs_amount - net) / net <= 0.02:
                            matched_run = run
                            match_type = "net"
                            break

                    # Check CPF slot
                    if run.cpf_payment_transaction_id is None:
                        cpf = float(run.cpf_payable_amount)
                        if cpf > 0 and abs(abs_amount - cpf) / cpf <= 0.02:
                            matched_run = run
                            match_type = "cpf"
                            break

                if not matched_run or not match_type:
                    continue

                now = datetime.now(UTC)
                txn.status = TransactionStatus.MATCHED
                txn.matched_at = now
                txn.categorized_by_logic = 'payroll_knockoff'

                # Handle cross-entity JEs via payroll service
                from src.services.payroll_service import payroll_service

                bank_account = db.query(FinanceBankAccount).get(txn.bank_account_id)
                primary_je = payroll_service.create_payroll_payment_entries(
                    db=db,
                    bank_account=bank_account,
                    payroll_run=matched_run,
                    txn_date=txn.transaction_date,
                    abs_amount=Decimal(str(abs_amount)),
                    match_type=match_type,
                )
                txn.reconciled_journal_entry_id = primary_je.id

                if match_type == "net":
                    matched_run.net_payment_transaction_id = txn.id
                else:
                    matched_run.cpf_payment_transaction_id = txn.id

                db.commit()

                results.append({
                    "transaction_id": txn.id,
                    "status": "categorized",
                    "rule_name": f"[payroll_knockoff:run_{matched_run.id}:{match_type}]",
                    "journal_entry_id": primary_je.id,
                    "cross_entity": entity_id != matched_run.entity_id,
                    "error": None,
                })
                handled.add(txn.id)

            except Exception as e:
                logger.warning(
                    f"Payroll knock-off check failed for transaction {txn.id}: {e}",
                    exc_info=True,
                )
                db.rollback()
                # Do not add to handled — let Phase 4 handle it normally

        return handled

    # ------------------------------------------------------------------
    # Phase 1: Counterparty enrichment  (L1 → L2 → L3)
    # ------------------------------------------------------------------

    def _enrich_counterparties(
        self,
        db: Session,
        transactions: list[FinanceTransaction],
    ) -> None:
        """
        Three-tier counterparty matching pipeline.

        L1 — Deterministic (exact / substring / alias):
          Fast O(n×m) scan. Runs for every transaction.
          Strategies (first match wins):
            1. Exact: lower(cp.name) == lower(raw_counterparty_name)
            2. Substring: lower(cp.name) in lower(description)
            3. Substring: lower(cp.name) in lower(raw_counterparty_name)
            4-6. Same three strategies against each alias in cp.aliases

        L2 — Fuzzy (rapidfuzz token_set_ratio ≥ L2_THRESHOLD):
          Tolerates abbreviations, extra tokens, minor typos.
          Matches raw_counterparty_name and description against cp.name and aliases.
          Only runs on transactions that L1 could not match.

        L3 — LLM (Claude haiku, single batched call):
          Sends all remaining unmatched transactions to the LLM in one request.
          The model chooses the best counterparty from the known list (or UNKNOWN).
          Only runs when at least one transaction survived L1 and L2.

        On any match: sets counterparty_id, counterparty_type, canonical name.
        Transactions already linked (counterparty_id set) are always skipped.
        """
        from src.models.counterparty import FinanceCounterparty

        if not transactions:
            return

        # Load active counterparties once for the whole batch
        counterparties = (
            db.query(FinanceCounterparty)
            .filter(FinanceCounterparty.status == "active")
            .all()
        )
        if not counterparties:
            return

        unmatched: list[FinanceTransaction] = []

        for txn in transactions:
            if txn.counterparty_id:
                continue  # already linked

            matched = self._match_l1(txn, counterparties)
            if matched is None:
                matched = self._match_l2(txn, counterparties)

            if matched:
                txn.counterparty_id = matched.id
                txn.counterparty_name = matched.name
            else:
                unmatched.append(txn)

        # L3: batch LLM call for remaining unmatched transactions
        if unmatched:
            self._match_l3_batch(unmatched, counterparties)

        # Flush enrichments so the accounting phase sees the updated counterparty_ids
        db.flush()

    # L2 fuzzy threshold: score must be ≥ this to accept a match (0-100)
    L2_THRESHOLD = 88

    def _match_l1(
        self,
        txn: FinanceTransaction,
        counterparties: list,
    ) -> Optional[Any]:
        """
        L1: deterministic exact/substring matching against name and aliases.
        Returns the first matching counterparty or None.
        """
        raw_cp = (txn.counterparty_name or "").lower().strip()
        raw_desc = (txn.description or "").lower().strip()

        for cp in counterparties:
            name_lower = cp.name.lower().strip()
            if not name_lower:
                continue

            # 1. Exact match on raw counterparty name from bank CSV
            if raw_cp and raw_cp == name_lower:
                return cp
            # 2. Counterparty name as substring in description
            if name_lower in raw_desc:
                return cp
            # 3. Counterparty name as substring in raw counterparty field
            if raw_cp and name_lower in raw_cp:
                return cp

            # 4-6. Same strategies against each alias
            for alias in [a.lower().strip() for a in (cp.aliases or []) if a]:
                if not alias:
                    continue
                if raw_cp and raw_cp == alias:
                    return cp
                if alias in raw_desc:
                    return cp
                if raw_cp and alias in raw_cp:
                    return cp

        return None

    def _match_l2(
        self,
        txn: FinanceTransaction,
        counterparties: list,
    ) -> Optional[Any]:
        """
        L2: fuzzy matching using rapidfuzz token_set_ratio.

        token_set_ratio handles extra tokens and word re-ordering well, making
        it suitable for bank descriptions like "GRAB SG-TXN-9182736" matching
        against counterparty name "Grab Singapore".

        Returns the highest-scoring counterparty above L2_THRESHOLD, or None.
        """
        try:
            from rapidfuzz import fuzz
        except ImportError:
            logger.warning("rapidfuzz not installed — L2 fuzzy matching skipped")
            return None

        raw_cp = (txn.counterparty_name or "").strip()
        raw_desc = (txn.description or "").strip()

        best_cp = None
        best_score = 0

        for cp in counterparties:
            # Gather all strings to match against for this counterparty
            candidates = [cp.name] + [a for a in (cp.aliases or []) if a]

            for candidate in candidates:
                for raw in [s for s in [raw_desc, raw_cp] if s]:
                    score = fuzz.token_set_ratio(raw.lower(), candidate.lower())
                    if score > best_score:
                        best_score = score
                        best_cp = cp

        if best_score >= self.L2_THRESHOLD:
            logger.debug(
                f"L2 match: txn {txn.id} → '{best_cp.name}' "
                f"(score={best_score}, desc='{txn.description[:50]}')"
            )
            return best_cp

        return None

    def _match_l3_batch(
        self,
        unmatched: list[FinanceTransaction],
        counterparties: list,
    ) -> None:
        """
        L3: batch LLM enrichment for transactions that survived L1 and L2.

        Sends a single Claude API call with all unmatched transaction descriptions
        and the full counterparty directory. The model returns a JSON mapping of
        transaction ID → counterparty ID (or null for UNKNOWN).

        Skips gracefully on API errors — transactions simply remain unenriched.
        """
        import os
        import json as json_lib

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.debug("ANTHROPIC_API_KEY not set — L3 LLM enrichment skipped")
            return

        try:
            import anthropic
        except ImportError:
            logger.warning("anthropic package not installed — L3 LLM enrichment skipped")
            return

        # Build the counterparty directory for the prompt
        cp_list = "\n".join(
            f"  {cp.id}: {cp.name} ({cp.type})"
            for cp in counterparties
        )

        # Build the transaction list for the prompt
        txn_list = "\n".join(
            f"  {txn.id}: desc=\"{txn.description}\" cp_field=\"{txn.counterparty_name or ''}\""
            for txn in unmatched
        )

        prompt = f"""You are a financial data enrichment engine. Match each bank transaction to the most likely counterparty from the known directory, based on the raw bank description and counterparty name field.

KNOWN COUNTERPARTIES (id: name (type)):
{cp_list}

TRANSACTIONS TO MATCH (id: desc="..." cp_field="..."):
{txn_list}

RULES:
- Return ONLY a JSON object mapping transaction IDs (as strings) to counterparty IDs (as integers) or null.
- Use null if no counterparty is a reasonable match.
- Be conservative — only match when you are confident (>80%).
- Do not invent or guess counterparties not in the list.

Example output format:
{{"123": 5, "124": null, "125": 12}}

Return only the JSON object, no explanation."""

        try:
            client = anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_response = message.content[0].text.strip()

            # Parse the JSON response
            # Strip markdown code fences if present
            if raw_response.startswith("```"):
                raw_response = raw_response.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            mapping: dict = json_lib.loads(raw_response)

            # Build a lookup for quick access
            cp_by_id = {cp.id: cp for cp in counterparties}

            matched_count = 0
            for txn in unmatched:
                cp_id = mapping.get(str(txn.id))
                if cp_id and isinstance(cp_id, int) and cp_id in cp_by_id:
                    cp = cp_by_id[cp_id]
                    txn.counterparty_id = cp.id
                    txn.counterparty_name = cp.name
                    matched_count += 1
                    logger.info(
                        f"L3 match: txn {txn.id} → '{cp.name}' "
                        f"(desc='{txn.description[:50]}')"
                    )

            logger.info(f"L3 enrichment: {matched_count}/{len(unmatched)} transactions matched")

        except Exception as e:
            logger.warning(f"L3 LLM enrichment failed: {e}", exc_info=True)
            # Fail gracefully — transactions remain unenriched, Phase 4 rules may still match them

    # ------------------------------------------------------------------
    # Phase 4A: Default account fallback
    # ------------------------------------------------------------------

    def _apply_default_account(
        self,
        db: Session,
        transaction: FinanceTransaction,
        counterparty: Any,
    ) -> Optional[dict[str, Any]]:
        """
        Create a journal entry using the counterparty's default_account_code.

        Called when a transaction has been enriched with a counterparty_id and
        that counterparty has a default_account_code. No rule is needed.

        Returns a result dict on success, None if the bank account has no COA code.
        """
        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == transaction.bank_account_id
        ).first()

        if not bank_account:
            raise ValueError(f"Bank account {transaction.bank_account_id} not found")
        if not bank_account.coa_account_code:
            # Can't create a JE without knowing the bank's COA code — fall through to rules
            return None

        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        abs_amount = abs(amount)

        entry = self._create_simple_entry(
            db=db,
            transaction=transaction,
            entity_id=bank_account.entity_id,
            bank_coa_code=bank_account.coa_account_code,
            contra_code=counterparty.default_account_code,
            amount=amount,
            abs_amount=abs_amount,
            source="counterparty_default",
        )

        transaction.status = TransactionStatus.MATCHED
        transaction.reconciled_journal_entry_id = entry.id
        transaction.matched_at = datetime.now(UTC)
        transaction.coa_account_code = counterparty.default_account_code

        db.commit()

        return {
            "transaction_id": transaction.id,
            "status": "categorized",
            "rule_name": f"[default:{counterparty.name}]",
            "journal_entry_id": entry.id,
            "error": None,
        }

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _match_transaction(
        self,
        transaction: FinanceTransaction,
        rules: list[FinanceCategorizationRule],
        cp_map: Optional[dict] = None,
    ) -> Optional[FinanceCategorizationRule]:
        """Walk rules in priority order; return first match."""
        for rule in rules:
            if self._rule_matches(transaction, rule, cp_map):
                return rule
        return None

    def _rule_matches(
        self,
        transaction: FinanceTransaction,
        rule: FinanceCategorizationRule,
        cp_map: Optional[dict] = None,
    ) -> bool:
        """Return True if ALL non-null criteria on the rule match the transaction."""
        amount = float(transaction.amount) if transaction.amount is not None else 0.0

        # 0. Counterparty ID (exact FK match — enrichment must have run first)
        if rule.counterparty_id is not None:
            if transaction.counterparty_id != rule.counterparty_id:
                return False

        # 0b. Counterparty type (match condition — check the counterparty's type)
        if rule.match_counterparty_type is not None:
            if not transaction.counterparty_id:
                return False  # No counterparty linked — cannot match type
            cp = (cp_map or {}).get(transaction.counterparty_id)
            if not cp:
                return False  # Counterparty not in map
            if cp.type != rule.match_counterparty_type:
                return False

        # 1. Bank account scope
        if rule.bank_account_ids:
            try:
                allowed_ids = json.loads(rule.bank_account_ids)
            except (json.JSONDecodeError, TypeError):
                allowed_ids = []
            if transaction.bank_account_id not in allowed_ids:
                return False

        # 2. Direction
        if rule.direction == TransactionDirection.INCOMING and amount <= 0:
            return False
        if rule.direction == TransactionDirection.OUTGOING and amount >= 0:
            return False

        # 3. Amount (absolute value)
        abs_amount = abs(amount)
        if rule.amount_operator is not None and rule.amount_value is not None:
            v = float(rule.amount_value)
            op = rule.amount_operator
            if op == AmountOperator.EQUALS and abs_amount != v:
                return False
            if op == AmountOperator.NOT_EQUALS and abs_amount == v:
                return False
            if op == AmountOperator.GREATER_THAN and abs_amount <= v:
                return False
            if op == AmountOperator.LESS_THAN and abs_amount >= v:
                return False
            if op == AmountOperator.BETWEEN:
                v_max = float(rule.amount_value_max) if rule.amount_value_max is not None else v
                if not (v <= abs_amount <= v_max):
                    return False

        # 4. Description
        if rule.description_operator is not None and rule.description_value is not None:
            if not _text_matches(transaction.description, rule.description_operator, rule.description_value):
                return False

        # 5. Transaction type
        if rule.transaction_type_operator is not None and rule.transaction_type_value is not None:
            if not _text_matches(transaction.transaction_type, rule.transaction_type_operator, rule.transaction_type_value):
                return False

        # 6. Counterparty name (from raw bank CSV)
        if rule.counterparty_operator is not None and rule.counterparty_value is not None:
            if not _text_matches(transaction.counterparty_name, rule.counterparty_operator, rule.counterparty_value):
                return False

        # 7. Currency
        if rule.match_currency is not None:
            if transaction.currency != rule.match_currency:
                return False

        return True

    # ------------------------------------------------------------------
    # Invoice rule matching (Phase 4 rules for AP invoices)
    # ------------------------------------------------------------------

    def match_invoice_to_rule(
        self,
        db: Session,
        counterparty_id: Optional[int],
        amount: float,
        currency: str,
        description: Optional[str] = None,
        counterparty_name: Optional[str] = None,
    ) -> Optional[FinanceCategorizationRule]:
        """
        Match an invoice against active EXPENSE/OUTGOING categorization rules.

        Used during invoice creation to determine the contra_account_code
        (expense account). Rules are evaluated BEFORE vendor default, contract COA,
        or AI suggestions.

        Only EXPENSE rules (direction=OUTGOING, category=EXPENSE) are considered,
        since invoices represent outgoing payments. Deposit, internal_transfer,
        and cross_entity_allocation rules are skipped.

        Matching criteria evaluated (same logic as transaction matching):
          - counterparty_value (text matching by vendor name)
          - match_counterparty_type (type matching: "vendor", "employee", etc.)
          - amount (absolute value comparison)
          - currency (exact match)
          - description (text matching against invoice notes/number)

        Args:
            db: Database session
            counterparty_id: The invoice's counterparty FK (or None)
            amount: The invoice total_amount (always positive for AP invoices)
            currency: ISO 4217 currency code
            description: Optional text to match against description rules
            counterparty_name: Optional counterparty name (fetched if not provided)

        Returns:
            The first matching rule, or None if no rules match.
        """
        # Fetch counterparty name and type if needed
        counterparty_type: Optional[str] = None
        if counterparty_id and not counterparty_name:
            cp = db.query(FinanceCounterparty).filter(
                FinanceCounterparty.id == counterparty_id
            ).first()
            if cp:
                counterparty_name = cp.name
                counterparty_type = cp.type

        rules = (
            db.query(FinanceCategorizationRule)
            .filter(
                FinanceCategorizationRule.status == RuleStatus.ACTIVE,
                FinanceCategorizationRule.direction == TransactionDirection.OUTGOING,
                FinanceCategorizationRule.category == TransactionCategory.EXPENSE,
            )
            .order_by(FinanceCategorizationRule.priority)
            .all()
        )

        for rule in rules:
            if self._invoice_rule_matches(
                rule, counterparty_name, counterparty_type, amount, currency, description
            ):
                return rule

        return None

    def _invoice_rule_matches(
        self,
        rule: FinanceCategorizationRule,
        counterparty_name: Optional[str],
        counterparty_type: Optional[str],
        amount: float,
        currency: str,
        description: Optional[str] = None,
    ) -> bool:
        """
        Check if ALL non-null criteria on the rule match the invoice data.

        Rules match invoices by TEXT and TYPE, not by numeric ID.
        - counterparty_value: text matching against vendor name
        - match_counterparty_type: type matching against vendor type (vendor, employee, etc.)

        This mirrors _rule_matches() logic but adapted for invoice fields.
        Bank account scope and transaction type are ignored for invoices.
        """
        # 0. Counterparty name matching (TEXT-based, not ID-based)
        if rule.counterparty_value is not None and rule.counterparty_operator is not None:
            if not _text_matches(counterparty_name, rule.counterparty_operator, rule.counterparty_value):
                return False

        # 0b. Counterparty type matching (e.g., "vendor", "employee")
        if rule.match_counterparty_type is not None:
            if counterparty_type != rule.match_counterparty_type:
                return False

        # 1. Bank account scope — not applicable for invoices, skip

        # 2. Direction — already filtered to OUTGOING in query, skip

        # 3. Amount (absolute value — invoices are always positive)
        abs_amount = abs(amount)
        if rule.amount_operator is not None and rule.amount_value is not None:
            v = float(rule.amount_value)
            op = rule.amount_operator
            if op == AmountOperator.EQUALS and abs_amount != v:
                return False
            if op == AmountOperator.NOT_EQUALS and abs_amount == v:
                return False
            if op == AmountOperator.GREATER_THAN and abs_amount <= v:
                return False
            if op == AmountOperator.LESS_THAN and abs_amount >= v:
                return False
            if op == AmountOperator.BETWEEN:
                v_max = float(rule.amount_value_max) if rule.amount_value_max is not None else v
                if not (v <= abs_amount <= v_max):
                    return False

        # 4. Description (match invoice notes/description against rule)
        if rule.description_operator is not None and rule.description_value is not None:
            if not _text_matches(description, rule.description_operator, rule.description_value):
                return False

        # 5. Transaction type — not applicable for invoices, skip

        # 6. Currency
        if rule.match_currency is not None:
            if currency != rule.match_currency:
                return False

        return True

    # ------------------------------------------------------------------
    # Conditional rule logic evaluation
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Apply rule
    # ------------------------------------------------------------------

    def _apply_rule(
        self,
        db: Session,
        transaction: FinanceTransaction,
        rule: FinanceCategorizationRule,
    ) -> dict[str, Any]:
        """Create journal entry, update transaction to MATCHED, apply tags."""
        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == transaction.bank_account_id
        ).first()

        if not bank_account:
            raise ValueError(f"Bank account {transaction.bank_account_id} not found")
        if not bank_account.coa_account_code:
            raise ValueError(
                f"Bank account {bank_account.id} ({bank_account.bank_name}) "
                f"has no COA account code configured"
            )

        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        abs_amount = abs(amount)

        # Get the contra_account_code from the rule
        # Note: INTERNAL_TRANSFER rules may not have a contra_account_code (they use target_bank_account_id instead)
        contra_account_code = None
        if rule.category != TransactionCategory.INTERNAL_TRANSFER:
            contra_account_code = rule.contra_account_code
            if not contra_account_code:
                raise ValueError(f"Rule {rule.id} produced no contra_account_code")

        if rule.category == TransactionCategory.INTERNAL_TRANSFER:
            journal_entry = self._create_internal_transfer_entries(
                db, transaction, rule, bank_account, amount, abs_amount
            )
        elif rule.category == TransactionCategory.CROSS_ENTITY_ALLOCATION:
            journal_entry = self._create_cross_entity_allocation_entries(
                db, transaction, rule, bank_account, abs_amount
            )
        else:
            journal_entry = self._create_simple_entry(
                db=db,
                transaction=transaction,
                entity_id=bank_account.entity_id,
                bank_coa_code=bank_account.coa_account_code,
                contra_code=contra_account_code,
                amount=amount,
                abs_amount=abs_amount,
                source="categorization_engine",
                rule=rule,
            )

        # Update transaction metadata
        if rule.counterparty_name:
            transaction.counterparty_name = rule.counterparty_name

        # Set COA account code for non-internal-transfer categorizations
        if rule.category != TransactionCategory.INTERNAL_TRANSFER:
            transaction.coa_account_code = contra_account_code

        # Set categorization type from rule category
        category_map = {
            TransactionCategory.EXPENSE: CategorizationType.EXPENSE,
            TransactionCategory.DEPOSIT: CategorizationType.DEPOSIT,
            TransactionCategory.INTERNAL_TRANSFER: CategorizationType.INTERNAL_TRANSFER,
            TransactionCategory.CROSS_ENTITY_ALLOCATION: CategorizationType.EXPENSE,  # Cross-entity is a specialized expense
        }
        if rule.category in category_map:
            transaction.categorization_type = category_map[rule.category]

        # For internal transfers: try to immediately pair with counter-transaction.
        # If counter not found yet → AWAITING_MATCH; the counter-transaction will
        # complete the pair when it arrives and Step 0 runs next time.
        if rule.category == TransactionCategory.INTERNAL_TRANSFER and rule.target_bank_account_id:
            counter_txn = self._find_counter_transaction(
                db, transaction, rule.target_bank_account_id
            )
            if counter_txn:
                # Pair both sides right now
                now = datetime.now(UTC)
                transaction.status = TransactionStatus.MATCHED
                transaction.reconciled_journal_entry_id = journal_entry.id
                transaction.matched_at = now
                counter_txn.status = TransactionStatus.MATCHED
                counter_txn.reconciled_journal_entry_id = journal_entry.id
                counter_txn.matched_at = now
                logger.info(
                    f"Internal transfer paired: txn {transaction.id} ↔ txn {counter_txn.id} "
                    f"via JE {journal_entry.id}"
                )
            else:
                # Counter not yet imported — wait
                transaction.status = TransactionStatus.AWAITING_MATCH
                transaction.reconciled_journal_entry_id = journal_entry.id
                transaction.expected_counterpart_ba_id = rule.target_bank_account_id
                logger.info(
                    f"Internal transfer awaiting counter: txn {transaction.id} "
                    f"waiting for ba={rule.target_bank_account_id}"
                )
        else:
            # Normal expense/deposit → MATCHED immediately
            transaction.status = TransactionStatus.MATCHED
            transaction.reconciled_journal_entry_id = journal_entry.id
            transaction.matched_at = datetime.now(UTC)
            # Track which rule was used for categorization
            transaction.categorized_by_rule_id = rule.id
            transaction.categorized_by_logic = 'rule'

        self._apply_tags(db, transaction.id, rule.tag_ids)
        db.commit()

        status_label = "awaiting_match" if transaction.status == TransactionStatus.AWAITING_MATCH else "categorized"
        return {
            "transaction_id": transaction.id,
            "status": status_label,
            "rule_name": rule.name,
            "journal_entry_id": journal_entry.id,
            "error": None,
        }

    # ------------------------------------------------------------------
    # Journal entry creation
    # ------------------------------------------------------------------

    def _create_simple_entry(
        self,
        db: Session,
        transaction: FinanceTransaction,
        entity_id: int,
        bank_coa_code: str,
        contra_code: str,
        amount: float,
        abs_amount: float,
        source: str = "categorization_engine",
        description_override: Optional[str] = None,
        rule: Optional[FinanceCategorizationRule] = None,
        gst_override: Optional[bool] = None,
    ) -> Any:
        """
        Create a 2-line (or 3-line with GST) journal entry.

        Money in  (amount >= 0): Debit bank,  Credit contra
        Money out (amount <  0): Debit contra, Credit bank

        If GST applies, creates a 3-line entry splitting GST from the amount.
        """
        je_description = description_override or transaction.description or "Categorized transaction"

        apply_gst = self._should_apply_gst(db, contra_code, rule, entity_id, gst_override)
        gst_rate = self._get_gst_rate(db, entity_id) if apply_gst else 0.0

        if apply_gst and gst_rate > 0:
            ex_gst = round(abs_amount / (1 + gst_rate), 2)
            gst_amount = round(abs_amount - ex_gst, 2)
            if amount < 0:
                lines = [
                    {"account_code": contra_code,        "debit_amount": ex_gst,    "credit_amount": 0.0,       "description": je_description},
                    {"account_code": GST_INPUT_TAX_CODE, "debit_amount": gst_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": bank_coa_code,      "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
            else:
                lines = [
                    {"account_code": bank_coa_code,       "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": contra_code,         "debit_amount": 0.0,        "credit_amount": ex_gst,    "description": je_description},
                    {"account_code": GST_OUTPUT_TAX_CODE, "debit_amount": 0.0,        "credit_amount": gst_amount, "description": je_description},
                ]
        else:
            if amount >= 0:
                lines = [
                    {"account_code": bank_coa_code, "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": contra_code,   "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
            else:
                lines = [
                    {"account_code": contra_code,   "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": bank_coa_code, "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]

        entry = journal_service.create(
            db=db,
            entity_id=entity_id,
            entry_date=transaction.transaction_date,
            description=je_description,
            lines=lines,
        )
        entry.source = source
        db.flush()
        return entry

    def _create_internal_transfer_entries(
        self,
        db: Session,
        transaction: FinanceTransaction,
        rule: FinanceCategorizationRule,
        bank_account: FinanceBankAccount,
        amount: float,
        abs_amount: float,
    ) -> Any:
        """
        Create journal entry/entries for an internal transfer.

        Same entity (intra-bank): single 2-line JE moving cash between accounts.
        Different entities (intercompany): paired JEs with a shared intercompany_group_id.
        The contra_account_code (if set on the rule) is used as the IC clearing account
        in both entities for intercompany transfers.
        """
        target_ba = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == rule.target_bank_account_id
        ).first()
        if not target_ba:
            raise ValueError(
                f"Target bank account {rule.target_bank_account_id} not found"
            )
        if not target_ba.coa_account_code:
            raise ValueError(
                f"Target bank account {target_ba.id} ({target_ba.bank_name}) "
                f"has no COA account code configured"
            )

        source_entity_id = bank_account.entity_id
        target_entity_id = target_ba.entity_id
        source_coa = bank_account.coa_account_code
        target_coa = target_ba.coa_account_code
        je_description = transaction.description or "Internal transfer"

        if source_entity_id == target_entity_id:
            # Intra-entity: one 2-line JE
            # Outgoing from source: Dr target_bank / Cr source_bank
            # Incoming to source:  Dr source_bank / Cr target_bank
            if amount < 0:
                lines = [
                    {"account_code": target_coa, "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": source_coa, "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
            else:
                lines = [
                    {"account_code": source_coa, "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": target_coa, "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
            entry = journal_service.create(
                db=db,
                entity_id=source_entity_id,
                entry_date=transaction.transaction_date,
                description=je_description,
                lines=lines,
            )
            entry.source = "categorization_engine"
            db.flush()
            return entry

        else:
            # Intercompany: two paired JEs
            ic_group_id = str(uuid.uuid4())
            ic_code = rule.contra_account_code  # IC clearing account used in both entities

            if not ic_code:
                raise ValueError(
                    "Intercompany internal_transfer rule requires contra_account_code "
                    "(IC clearing account used in both entities)"
                )

            if amount < 0:
                # Source entity sends money out: Dr IC Receivable / Cr Source Bank
                source_lines = [
                    {"account_code": ic_code,    "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": source_coa, "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
                # Target entity receives money in: Dr Target Bank / Cr IC Payable
                target_lines = [
                    {"account_code": target_coa, "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": ic_code,    "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
            else:
                # Source entity receives money in: Dr Source Bank / Cr IC Payable
                source_lines = [
                    {"account_code": source_coa, "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": ic_code,    "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]
                # Target entity sends money out: Dr IC Receivable / Cr Target Bank
                target_lines = [
                    {"account_code": ic_code,    "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": target_coa, "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
                ]

            source_entry = journal_service.create(
                db=db, entity_id=source_entity_id,
                entry_date=transaction.transaction_date,
                description=je_description, lines=source_lines,
            )
            source_entry.source = "categorization_engine"
            source_entry.intercompany_group_id = ic_group_id

            target_entry = journal_service.create(
                db=db, entity_id=target_entity_id,
                entry_date=transaction.transaction_date,
                description=je_description, lines=target_lines,
            )
            target_entry.source = "categorization_engine"
            target_entry.intercompany_group_id = ic_group_id

            db.flush()
            return source_entry

    def _create_cross_entity_allocation_entries(
        self,
        db: Session,
        transaction: FinanceTransaction,
        rule: FinanceCategorizationRule,
        bank_account: FinanceBankAccount,
        abs_amount: float,
    ) -> Any:
        """
        Create paired intercompany JEs for a cross-entity cost allocation.

        Scenario: Entity A (bank) pays an expense that economically belongs to Entity B.
          Entity A (bank entity):      Dr IC Receivable / Cr Bank
          Entity B (allocation entity): Dr Expense       / Cr IC Payable

        rule.contra_account_code  → expense account code on Entity B
        rule.allocation_entity_id → Entity B's ID
        IC codes are resolved via the same lookup table used by invoice_service.
        """
        from src.services.invoice_service import _IC_RECEIVABLE_CODES, _IC_PAYABLE_CODES, _entity_short
        from src.models.entity import FinanceEntity

        if not rule.allocation_entity_id:
            raise ValueError("cross_entity_allocation rule missing allocation_entity_id")
        if not rule.contra_account_code:
            raise ValueError("cross_entity_allocation rule missing contra_account_code")

        bank_entity = db.query(FinanceEntity).filter(
            FinanceEntity.id == bank_account.entity_id
        ).first()
        alloc_entity = db.query(FinanceEntity).filter(
            FinanceEntity.id == rule.allocation_entity_id
        ).first()

        if not bank_entity:
            raise ValueError(f"Bank entity {bank_account.entity_id} not found")
        if not alloc_entity:
            raise ValueError(f"Allocation entity {rule.allocation_entity_id} not found")

        bank_short = _entity_short(bank_entity.name)
        alloc_short = _entity_short(alloc_entity.name)
        pair = (bank_short, alloc_short)

        ic_recv_code = _IC_RECEIVABLE_CODES.get(pair)
        ic_pay_code = _IC_PAYABLE_CODES.get((alloc_short, bank_short))

        if not ic_recv_code or not ic_pay_code:
            raise ValueError(
                f"No IC account codes defined for entity pair ({bank_short}, {alloc_short}). "
                f"Add entries to _IC_RECEIVABLE_CODES / _IC_PAYABLE_CODES in invoice_service.py"
            )

        je_description = transaction.description or "Cross-entity cost allocation"
        ic_group_id = str(uuid.uuid4())

        # Bank entity: pays out cash → Dr IC Receivable (asset: they are owed by alloc entity)
        #                             Cr Bank
        bank_lines = [
            {"account_code": ic_recv_code,               "debit_amount": abs_amount, "credit_amount": 0.0,        "description": je_description},
            {"account_code": bank_account.coa_account_code, "debit_amount": 0.0,      "credit_amount": abs_amount, "description": je_description},
        ]

        # Allocation entity: bears the cost → Dr Expense (contra_account_code)
        #                                      Cr IC Payable (they owe the bank entity)
        alloc_lines = [
            {"account_code": rule.contra_account_code, "debit_amount": abs_amount, "credit_amount": 0.0,        "description": je_description},
            {"account_code": ic_pay_code,              "debit_amount": 0.0,        "credit_amount": abs_amount, "description": je_description},
        ]

        bank_entry = journal_service.create(
            db=db,
            entity_id=bank_account.entity_id,
            entry_date=transaction.transaction_date,
            description=je_description,
            lines=bank_lines,
        )
        bank_entry.source = "categorization_engine"
        bank_entry.intercompany_group_id = ic_group_id

        alloc_entry = journal_service.create(
            db=db,
            entity_id=rule.allocation_entity_id,
            entry_date=transaction.transaction_date,
            description=je_description,
            lines=alloc_lines,
        )
        alloc_entry.source = "categorization_engine"
        alloc_entry.intercompany_group_id = ic_group_id

        db.flush()
        return bank_entry

    # ------------------------------------------------------------------
    # GST helpers
    # ------------------------------------------------------------------

    def _should_apply_gst(
        self,
        db: Session,
        contra_code: str,
        rule: Optional[FinanceCategorizationRule],
        entity_id: int,
        gst_override: Optional[bool] = None,
    ) -> bool:
        """Priority: explicit override > rule override > account.gst_applicable"""
        if gst_override is not None:
            return gst_override
        if rule is not None and rule.gst_override is not None:
            return rule.gst_override
        account = db.query(FinanceAccount).filter(FinanceAccount.code == contra_code).first()
        return bool(account and account.gst_applicable)

    def _get_gst_rate(self, db: Session, entity_id: int) -> float:
        from src.models.entity import FinanceEntity
        entity = db.query(FinanceEntity).filter(FinanceEntity.id == entity_id).first()
        return float(entity.gst_rate) if entity and entity.gst_rate else 0.0

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def _apply_tags(self, db: Session, transaction_id: int, tag_ids_json: Optional[str]) -> None:
        if not tag_ids_json:
            return
        try:
            tag_ids = json.loads(tag_ids_json)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(tag_ids, list):
            return
        for tag_id in tag_ids:
            if not isinstance(tag_id, int):
                continue
            existing = db.query(FinanceTransactionTag).filter(
                FinanceTransactionTag.transaction_id == transaction_id,
                FinanceTransactionTag.tag_id == tag_id,
            ).first()
            if not existing:
                db.add(FinanceTransactionTag(transaction_id=transaction_id, tag_id=tag_id))

    # ------------------------------------------------------------------
    # Manual categorization (human-driven → straight to RECONCILED)
    # ------------------------------------------------------------------

    def manual_categorize(
        self,
        db: Session,
        transaction_id: int,
        contra_account_code: str,
        counterparty_id: Optional[int] = None,
        counterparty_name: Optional[str] = None,
        counterparty_type: Optional[str] = None,
        save_as_default: bool = False,
        tag_ids: Optional[list[int]] = None,
        description: Optional[str] = None,
        gst_override: Optional[bool] = None,
    ) -> dict[str, Any]:
        """
        Manually categorize a single transaction.

        Human-driven: bypasses the rules engine and goes directly to RECONCILED
        (no separate confirmation step needed — the human IS the confirmation).

        Raises:
            ValueError: If transaction not found, not pending, or account code invalid.
        """
        transaction = db.query(FinanceTransaction).filter(
            FinanceTransaction.id == transaction_id
        ).first()
        if not transaction:
            raise ValueError(f"Transaction with ID {transaction_id} not found")
        if transaction.status != TransactionStatus.PENDING:
            raise ValueError(
                f"Transaction {transaction_id} is not in Pending status "
                f"(current: {transaction.status.value})"
            )

        account = db.query(FinanceAccount).filter(FinanceAccount.code == contra_account_code).first()
        if not account:
            raise ValueError(f"Account code '{contra_account_code}' does not exist")

        bank_account = db.query(FinanceBankAccount).filter(
            FinanceBankAccount.id == transaction.bank_account_id
        ).first()
        if not bank_account:
            raise ValueError(f"Bank account {transaction.bank_account_id} not found")
        if not bank_account.coa_account_code:
            raise ValueError(
                f"Bank account {bank_account.id} ({bank_account.bank_name}) "
                f"has no COA account code configured"
            )

        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        abs_amount = abs(amount)

        entry = self._create_simple_entry(
            db=db,
            transaction=transaction,
            entity_id=bank_account.entity_id,
            bank_coa_code=bank_account.coa_account_code,
            contra_code=contra_account_code,
            amount=amount,
            abs_amount=abs_amount,
            source="manual",
            description_override=description,
            gst_override=gst_override,
        )

        # Counterparty resolution — FK takes precedence over free-text fields
        if counterparty_id:
            from src.models.counterparty import FinanceCounterparty
            cp = db.get(FinanceCounterparty, counterparty_id)
            if cp:
                transaction.counterparty_id = cp.id
                transaction.counterparty_name = cp.name
                # Optionally persist this account as the counterparty's default
                if save_as_default:
                    cp.default_account_code = contra_account_code
        else:
            if counterparty_name:
                transaction.counterparty_name = counterparty_name

        # Manual categorization = human confirmation → RECONCILED directly
        transaction.status = TransactionStatus.RECONCILED
        transaction.reconciled_journal_entry_id = entry.id
        transaction.coa_account_code = contra_account_code
        transaction.reconciled_at = datetime.now(UTC)

        if tag_ids:
            self._apply_tags(db, transaction.id, json.dumps(tag_ids))

        db.commit()

        return {
            "transaction_id": transaction.id,
            "journal_entry_id": entry.id,
            "status": "categorized",
        }

    # ------------------------------------------------------------------
    # Phase 4: AI classification fallback
    # ------------------------------------------------------------------

    def _run_ai_classification(
        self,
        db: Session,
        transactions: list[FinanceTransaction],
    ) -> dict[int, dict]:
        """
        Batch-classify unmatched transactions via Claude Haiku.

        Sends all unmatched transactions in a single API call with the entity's
        active COA as context. Claude returns a confidence-scored suggestion per
        transaction.

        confidence ≥ 0.80 → create JE → transaction → MATCHED
        confidence < 0.80 → NEEDS_REVIEW; ai fields stored, no JE yet
        No API key / error → returns empty dict (transactions stay PENDING)

        Returns a dict keyed by transaction_id with result dicts ready to merge
        into the main results list.
        """
        import os
        import json as json_lib

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return {}

        if not transactions:
            return {}

        try:
            import anthropic

            # Collect entity IDs present in this batch (may be multiple)
            ba_ids = {t.bank_account_id for t in transactions}
            bank_accounts: dict[int, FinanceBankAccount] = {}
            for ba in db.query(FinanceBankAccount).filter(
                FinanceBankAccount.id.in_(ba_ids)
            ).all():
                bank_accounts[ba.id] = ba

            # Build entity COA context (active accounts, one entity at a time)
            entity_ids = {ba.entity_id for ba in bank_accounts.values()}
            from src.models.account import FinanceAccount
            coa_by_entity: dict[int, list[dict]] = {}
            for entity_id in entity_ids:
                accounts = (
                    db.query(FinanceAccount)
                    .filter(
                        FinanceAccount.entity_id == entity_id,
                        FinanceAccount.status == "active",
                    )
                    .order_by(FinanceAccount.code)
                    .all()
                )
                coa_by_entity[entity_id] = [
                    {"code": a.code, "name": a.name, "type": a.account_type.value
                     if hasattr(a.account_type, "value") else str(a.account_type)}
                    for a in accounts
                ]

            # Build transaction payloads
            txn_payloads = []
            for txn in transactions:
                ba = bank_accounts.get(txn.bank_account_id)
                entity_id = ba.entity_id if ba else None
                txn_payloads.append({
                    "id": txn.id,
                    "description": txn.description or "",
                    "amount": float(txn.amount),
                    "currency": txn.currency,
                    "direction": "outgoing" if float(txn.amount) < 0 else "incoming",
                    "counterparty": txn.counterparty_name or "",
                    "bank_account": ba.account_name if ba else "",
                    "entity_id": entity_id,
                    "coa": coa_by_entity.get(entity_id, []),
                })

            prompt = f"""You are a finance classification engine. Classify each bank transaction
to the most appropriate account in the chart of accounts.

Transactions to classify:
{json_lib.dumps(txn_payloads, indent=2)}

For each transaction, return a JSON array (one object per transaction) with:
{{
  "id": <transaction id>,
  "account_code": "<code from the coa field for that transaction>",
  "confidence": <0.00–1.00 — your confidence in this classification>,
  "reasoning": "<1 sentence plain-English explanation>"
}}

Rules:
- account_code MUST be from the coa list provided for that transaction's entity_id
- confidence >= 0.80 means you are confident; < 0.80 means uncertain
- For intercompany or payroll transactions that don't clearly fit any account, use confidence 0.50
- Return ONLY the JSON array, no other text"""

            client = anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            suggestions = json_lib.loads(raw)
            if not isinstance(suggestions, list):
                raise ValueError("Expected JSON array from AI classification")

            # Index suggestions by transaction id
            suggestion_map: dict[int, dict] = {s["id"]: s for s in suggestions if "id" in s}

        except Exception as e:
            logger.error(f"AI classification fallback error: {e}", exc_info=True)
            return {}

        results: dict[int, dict] = {}
        now = datetime.now(UTC)

        for txn in transactions:
            suggestion = suggestion_map.get(txn.id)
            if not suggestion:
                continue

            account_code = suggestion.get("account_code")
            confidence = float(suggestion.get("confidence", 0.0))
            reasoning = suggestion.get("reasoning", "")

            if not account_code:
                continue

            # Store AI fields on the transaction regardless of outcome
            txn.ai_suggested_account_code = account_code
            txn.ai_confidence = round(confidence, 3)
            txn.ai_reasoning = reasoning

            if confidence >= 0.80:
                # High confidence — create JE and MATCH
                try:
                    ba = bank_accounts.get(txn.bank_account_id)
                    if not ba or not ba.coa_account_code:
                        raise ValueError("No bank COA code")

                    amount = float(txn.amount)
                    abs_amount = abs(amount)
                    je = self._create_simple_entry(
                        db=db,
                        transaction=txn,
                        entity_id=ba.entity_id,
                        bank_coa_code=ba.coa_account_code,
                        contra_code=account_code,
                        amount=amount,
                        abs_amount=abs_amount,
                        source="ai_classification",
                    )
                    txn.status = TransactionStatus.MATCHED
                    txn.reconciled_journal_entry_id = je.id
                    txn.matched_at = now
                    txn.coa_account_code = account_code
                    db.commit()

                    results[txn.id] = {
                        "transaction_id": txn.id,
                        "status": "categorized",
                        "rule_name": f"[ai:confidence={confidence:.2f}]",
                        "journal_entry_id": je.id,
                        "error": None,
                    }
                except Exception as je_err:
                    logger.error(
                        f"AI classification: JE creation failed for txn {txn.id}: {je_err}",
                        exc_info=True,
                    )
                    db.rollback()
            else:
                # Low confidence — NEEDS_REVIEW
                txn.status = TransactionStatus.NEEDS_REVIEW
                db.commit()

                results[txn.id] = {
                    "transaction_id": txn.id,
                    "status": "needs_review",
                    "rule_name": f"[ai:confidence={confidence:.2f}]",
                    "journal_entry_id": None,
                    "error": None,
                    "ai_suggested_account_code": account_code,
                    "ai_confidence": confidence,
                    "ai_reasoning": reasoning,
                }

        return results


# ------------------------------------------------------------------
# Text matching helper
# ------------------------------------------------------------------

def _text_matches(value: Optional[str], operator: MatchOperator, pattern: str) -> bool:
    """
    Apply a MatchOperator comparison between a field value and a pattern string.

    None field value:
      - NOT_CONTAINS → True  (null doesn't contain anything)
      - everything else → False
    """
    if value is None:
        return operator == MatchOperator.NOT_CONTAINS

    v_lower = value.lower()
    p_lower = pattern.lower()

    if operator == MatchOperator.CONTAINS:
        return p_lower in v_lower
    if operator == MatchOperator.NOT_CONTAINS:
        return p_lower not in v_lower
    if operator == MatchOperator.IS_EXACTLY:
        return v_lower == p_lower
    if operator == MatchOperator.MATCHES_REGEX:
        try:
            return bool(re.search(pattern, value, re.IGNORECASE))
        except re.error:
            return p_lower in v_lower  # fallback: treat as substring
    return False


# Singleton instance
categorization_service = CategorizationService()
