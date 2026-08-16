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
from datetime import datetime, date, UTC
from decimal import Decimal
from typing import Optional, Any, TYPE_CHECKING, cast
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from anthropic.types import TextBlock

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

# Books-open date (POL-28). The engine NEVER categorizes a transaction dated
# before this — pre-2026 rows (e.g. the 358 all-history Stripe payouts) are
# already inside the opening balances, so booking them would double-count.
# They belong to the Phase B historical replay, not Phase A (Gaurav 2026-07-27).
BOOKS_OPEN_DATE = date(2026, 1, 1)

# POL-34 direction guard: every counterparty TYPE has a normal money direction.
# A default account may auto-book ONLY when the money flows that way; a mismatch
# (a vendor sending us money = a refund; us paying an investor = a repayment) is
# an exception that must go to review, never book blind against the default.
# 'out' = we pay them (amount < 0); 'in' = they pay us (amount > 0); None = both.
CP_TYPE_NORMAL_DIRECTION = {
    "vendor": "out",
    "employee": "out",
    "government": "out",
    "investor": "in",
    "bank": None,      # fees out, interest in — allow both
}


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
        txn_ids: Optional[list[int]] = None,
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

        Every run writes a finance_sync_runs receipt (source='categorization'):
        fetched=processed, created=categorized, duplicates=uncategorized —
        so run history and hit-rates are queryable (Gaurav, 2026-07-25).
        """
        from src.models.sync_run import start_run, finish_run
        _run_receipt = start_run(db, "categorization", entity_id=entity_id,
                                 bank_account_id=bank_account_id)
        try:
            _summary = self._run_inner(db, entity_id, bank_account_id, rule_id, limit, txn_ids)
        except Exception as _e:
            finish_run(db, _run_receipt, error=_e)
            raise
        _results = _summary.get("results") or []
        _by_status: dict[str, int] = {}
        for _r in _results:
            _k = str(_r.get("status") or "?")
            _by_status[_k] = _by_status.get(_k, 0) + 1
        _total = _summary.get("total_processed") or 0
        finish_run(db, _run_receipt,
                   fetched=_total,
                   created=_summary.get("categorized"),
                   duplicates=_summary.get("uncategorized"),
                   error=(f"{_summary.get('errors')} txn errors" if _summary.get("errors") else None),
                   detail={
                       "by_status": _by_status,
                       "pct_categorized": round(100 * (_summary.get("categorized") or 0) / _total, 1) if _total else 0,
                   })
        return _summary

    def _run_inner(
        self,
        db: Session,
        entity_id: Optional[int] = None,
        bank_account_id: Optional[int] = None,
        rule_id: Optional[int] = None,
        limit: int = 100,
        txn_ids: Optional[list[int]] = None,
    ) -> dict[str, Any]:
        """(see run() docstring)"""
        query = db.query(FinanceTransaction).filter(
            FinanceTransaction.status.in_([TransactionStatus.PENDING, TransactionStatus.IMPORTED]),
            # POL-28 Phase-A floor: never categorize pre-books-open rows (they
            # live in the opening balances; categorizing = double-count). This
            # holds even under an explicit txn_ids selection — Phase B only.
            FinanceTransaction.transaction_date >= BOOKS_OPEN_DATE,
        )
        if txn_ids:
            # bulk-selection scope: process EXACTLY these ids, nothing else
            query = query.filter(FinanceTransaction.id.in_(txn_ids))

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

        # Staged (IMPORTED) txns being processed are now "run on" → mark them PENDING so
        # the rest of the pipeline (all the `== PENDING` filters) sees them, and IMPORTED
        # strictly means "engine never ran". Out-of-scope IMPORTED txns stay staged.
        _staged = [t for t in transactions if t.status == TransactionStatus.IMPORTED]
        for t in _staged:
            t.status = TransactionStatus.PENDING
        if _staged:
            db.flush()

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

        # ── Phase 0.5: Classify NEW internal transfers BEFORE enrichment ───
        # Internal-transfer rules are deterministic + counterparty-INDEPENDENT
        # (they match raw description/amount, never the enriched counterparty),
        # so per the cascade principle they run before Phase 1. This claims
        # transfers up front: they never get a (wrong) external-party
        # counterparty written, and never reach the expensive L3 LLM.
        if transactions:
            # Intercompany rules (POL-27) ride the same protected lane: both are
            # deterministic, counterparty-independent, and must claim BEFORE
            # enrichment/AI can misread a cross-entity movement as P&L.
            transfer_rules = (
                db.query(FinanceCategorizationRule)
                .filter(
                    FinanceCategorizationRule.status == RuleStatus.ACTIVE,
                    FinanceCategorizationRule.category.in_([
                        TransactionCategory.INTERNAL_TRANSFER,
                        TransactionCategory.INTERCOMPANY_TRANSFER,
                    ]),
                )
                .order_by(FinanceCategorizationRule.priority)
                .all()
            )
            if rule_id is not None:
                transfer_rules = [r for r in transfer_rules if r.id == rule_id]
            for transaction in list(transactions):
                if transaction.status != TransactionStatus.PENDING:
                    # already handled this run (e.g. paired as the counter-leg
                    # of a transfer booked earlier in this same loop) — never
                    # re-book it (the double-JE bulk-import edge)
                    continue
                matched_rule = self._match_transaction(transaction, transfer_rules, {})
                if not matched_rule:
                    continue
                try:
                    results.append(self._apply_rule(db, transaction, matched_rule))
                    categorized += 1
                except Exception as e:
                    logger.error(
                        f"Error classifying internal transfer txn {transaction.id}: {e}",
                        exc_info=True,
                    )
                    db.rollback()
            # Drop everything no longer PENDING: claimed transfers (AWAITING_MATCH)
            # plus any counter-legs paired immediately (MATCHED). Enrichment +
            # later phases only see what's left.
            transactions = [t for t in transactions if t.status == TransactionStatus.PENDING]

        # ── Phase 1: Counterparty enrichment ──────────────────────────────
        self._enrich_counterparties(db, transactions)

        # ── Rung 1: Transfer-ID knock-off (unified, deterministic, FX-aware) ──
        # Anything we paid THROUGH OUR SYSTEM carries a wise_transfer_id that pairs to an
        # awaiting_import payout (invoice/claim/payroll alike). Settle it deterministically and
        # currency-agnostically BEFORE the amount-based fallbacks below.
        transfer_id_handled: set[int] = self._try_transfer_id_knockoff(db, transactions, results)
        categorized += len(transfer_id_handled)
        transactions = [t for t in transactions if t.id not in transfer_id_handled]

        # ── Phase 2: AP Knock-off ────────────────────────────────────────
        # For any transaction whose counterparty was just enriched, check
        # whether it matches an open AP invoice. If so, create the payment
        # JE (Dr AP / Cr Bank) and mark the transaction handled so Phase 4
        # does not double-book the expense.
        ap_handled_ids: set[int] = self._try_ap_knockoff(db, transactions, results, categorized)
        categorized += len(ap_handled_ids)

        # ── Phase 3: Payroll Knock-off — RETIRED (item 3, 2026-08-16) ────
        # The old run-based amount match (_try_payroll_knockoff) is superseded: system-paid payroll
        # settles via Rung 1 (transfer-id, incl. cross-entity), and the outside-system same-entity
        # fallback is the register knock-off (Phase 3.6). One payroll knock-off, not two.
        payroll_handled_ids: set[int] = set()

        # ── Phase 3.5: Employee-claim Knock-off (POL-139 cat 4) ──────────
        # An outgoing reimbursement that settles an approved employee claim → post
        # Dr 2303 / Cr bank and mark the claim PAID, instead of Phase-4 rules.
        claim_handled_ids: set[int] = self._try_claim_knockoff(db, transactions, results)
        categorized += len(claim_handled_ids)

        # ── Phase 3.6: Payroll register-payout Knock-off (PR-4b, POL-139) ─
        # A fanned-out payroll payable (net→2304 / statutory→2300/2302/2305) settled by its bank
        # payment → post Dr <liability> / Cr bank and mark the payout POSTED.
        payroll_reg_ids: set[int] = self._try_payroll_register_knockoff(db, transactions, results)
        categorized += len(payroll_reg_ids)

        # ── Phase 3.7: Invoice register-payout Knock-off (paid-outside AP) ─
        # A paid-OUTSIDE invoice (mark_paid_already → RECONCILE payout) settled by its bank line →
        # settle via the FX-aware AP path and mark the payout POSTED. Without this, paid-outside
        # invoices accumulate unmatched bank lines forever.
        invoice_reg_ids: set[int] = self._try_invoice_register_knockoff(db, transactions, results)
        categorized += len(invoice_reg_ids)

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
                    # Use counterparty's default account — but only when the money
                    # flows in this counterparty-type's normal direction (POL-34).
                    if cp.default_account_code and self._default_direction_ok(cp, transaction):
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
            if target_ba_id is None:
                # claim-only waiter (target-less rule): it gets attached by the
                # KNOWING side's counter-search, never by Phase 0 itself
                continue
            candidates = pending_by_ba.get(target_ba_id, [])
            if not candidates:
                continue

            counter = self._pick_counter(
                db, waiting_txn,
                [c for c in candidates if c.id not in handled_ids],
            )
            if not counter:
                continue

            # Pair them
            now = datetime.now(UTC)
            je_id = waiting_txn.reconciled_journal_entry_id
            # Cross-ccy transfer deferred its JE (je_id is None) → mint the FX-plug entry now that both
            # legs are known (POL-141/142); same-ccy legs share the waiting leg's existing JE.
            if je_id is None:
                out_leg, in_leg = ((waiting_txn, counter) if float(waiting_txn.amount) < 0
                                   else (counter, waiting_txn))
                je_id = self._create_fx_transfer_je(db, out_leg, in_leg).id
                waiting_txn.reconciled_journal_entry_id = je_id
                counter.categorized_by_logic = 'transfer_pairing_fx'
            else:
                counter.categorized_by_logic = 'transfer_pairing'

            waiting_txn.status = TransactionStatus.MATCHED
            waiting_txn.matched_at = now
            waiting_txn.expected_counterpart_ba_id = None  # cleared — no longer waiting

            counter.status = TransactionStatus.MATCHED
            counter.reconciled_journal_entry_id = je_id
            counter.matched_at = now
            counter.categorization_type = CategorizationType.INTERNAL_TRANSFER

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

    @staticmethod
    def _ref_tokens(description: Optional[str]) -> set[str]:
        """Long alphanumeric tokens (bank references like CT0038530178) — the
        only description content stable across both statement legs (DQ-14)."""
        import re
        return set(re.findall(r"[A-Z0-9]{7,}", (description or "").upper()))

    def _pick_counter(
        self,
        db: Session,
        waiting_txn: FinanceTransaction,
        candidates: list[FinanceTransaction],
    ) -> Optional[FinanceTransaction]:
        """Select THE counter-leg among candidates, or None.

        Hard filters: different bank account (a transfer's legs can never live
        in one account), opposite sign, amount within ±2%, date within ±5 days.
        When several candidates survive (e.g. two identical 10k transfers on
        the same day — the Apr-11 mispair), prefer a shared bank-reference
        token, then the tightest date. If the tie still can't be broken by
        evidence, REFUSE to guess: the leg stays awaiting for a human.
        """
        waiting_amount = float(waiting_txn.amount)
        abs_waiting = abs(waiting_amount)
        if abs_waiting == 0:
            return None

        viable = []
        for c in candidates:
            if c.bank_account_id == waiting_txn.bank_account_id:
                continue  # same-account "pair" is an impossibility
            cand_amount = float(c.amount)
            if waiting_amount * cand_amount >= 0:
                continue  # same sign — not a counter
            # POL-141/142: currency-aware amount match. Same ccy → tight ±2%. Cross-currency (e.g. a USD
            # leg vs its SGD counterpart) → convert the candidate INTO the waiting leg's currency at the
            # monthly rate, then a LOOSER ±5% band absorbs the bank spot-vs-our-rate spread.
            w_ccy = waiting_txn.currency
            c_ccy = c.currency
            cand_cmp, tol = abs(cand_amount), 0.02
            if w_ccy and c_ccy and w_ccy != c_ccy:
                try:
                    from src.services.fx_service import fx_service
                    rate = fx_service.get_monthly_rate(db, c_ccy, w_ccy, c.transaction_date)
                    if rate is None:
                        continue  # no rate on file — can't compare across currencies, skip (POL-26)
                    cand_cmp = abs(cand_amount) * float(rate)
                    tol = 0.05
                except Exception as e:
                    # a DB/rate-lookup error must not SILENTLY drop this candidate for every pair — log it
                    logger.warning("cross-currency rate lookup failed (%s->%s): %s", c_ccy, w_ccy, e)
                    continue
            if abs(cand_cmp - abs_waiting) / abs_waiting > tol:
                continue
            if abs((c.transaction_date - waiting_txn.transaction_date).days) > 5:
                continue
            viable.append(c)

        if not viable:
            return None
        if len(viable) == 1:
            return viable[0]

        # Ambiguous — discriminate on shared reference tokens first
        w_tokens = self._ref_tokens(waiting_txn.description)
        if w_tokens:
            token_hits = [c for c in viable if w_tokens & self._ref_tokens(c.description)]
            if len(token_hits) == 1:
                return token_hits[0]
            if token_hits:
                viable = token_hits

        # Then the tightest date
        best_gap = min(abs((c.transaction_date - waiting_txn.transaction_date).days)
                       for c in viable)
        closest = [c for c in viable
                   if abs((c.transaction_date - waiting_txn.transaction_date).days) == best_gap]
        if len(closest) == 1:
            return closest[0]

        logger.warning(
            f"Pairing ambiguity: txn {waiting_txn.id} has {len(closest)} equally "
            f"plausible counters ({[c.id for c in closest]}) — refusing to guess")
        return None

    def _find_counter_transaction(
        self,
        db: Session,
        txn: FinanceTransaction,
        target_ba_id: int,
    ) -> Optional[FinanceTransaction]:
        """
        Look for a counter-transaction on target_ba_id that mirrors txn.

        Candidates are PENDING or IMPORTED lines (IMPORTED: staged rows outside
        the current run's scope — e.g. a Dec-31 payout whose bank leg lands
        Jan-2 — are legitimate counters; the Jan-2026 25k sat unpaired for
        exactly this reason) — or CLAIM-ONLY waiters: transactions a
        target-less transfer rule claimed as AWAITING_MATCH without a JE (the
        two-rules-per-corridor law: the side that doesn't know its counterpart
        claims the transfer and waits to be attached to the knowing side's JE).

        Selection + ambiguity law live in _pick_counter.
        """
        from datetime import timedelta
        from sqlalchemy import and_, or_

        if abs(float(txn.amount)) == 0:
            return None

        date_low = txn.transaction_date - timedelta(days=5)
        date_high = txn.transaction_date + timedelta(days=5)

        candidates = db.query(FinanceTransaction).filter(
            FinanceTransaction.bank_account_id == target_ba_id,
            or_(
                FinanceTransaction.status.in_(
                    [TransactionStatus.PENDING, TransactionStatus.IMPORTED]),
                and_(
                    FinanceTransaction.status == TransactionStatus.AWAITING_MATCH,
                    FinanceTransaction.reconciled_journal_entry_id.is_(None),
                ),
            ),
            FinanceTransaction.transaction_date.between(date_low, date_high),
        ).all()

        return self._pick_counter(db, txn, candidates)

    def _find_awaiting_mirror_je(
        self,
        db: Session,
        txn: FinanceTransaction,
        target_ba_id: int,
    ) -> Optional[FinanceTransaction]:
        """The OTHER side already booked this movement: an AWAITING txn on the
        target account WITH a JE, expecting a counterpart from OUR account,
        opposite amount. Attaching to that JE instead of creating our own kills
        the both-sides-know duplicate (Apr-11 JEs 3218+2480 were this class).
        """
        from datetime import timedelta

        if abs(float(txn.amount)) == 0:
            return None
        date_low = txn.transaction_date - timedelta(days=5)
        date_high = txn.transaction_date + timedelta(days=5)
        candidates = db.query(FinanceTransaction).filter(
            FinanceTransaction.bank_account_id == target_ba_id,
            FinanceTransaction.status == TransactionStatus.AWAITING_MATCH,
            FinanceTransaction.reconciled_journal_entry_id.isnot(None),
            FinanceTransaction.expected_counterpart_ba_id == txn.bank_account_id,
            FinanceTransaction.transaction_date.between(date_low, date_high),
        ).all()
        return self._pick_counter(db, txn, candidates)

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

        handled: set[int] = set()

        for txn in transactions:
            if not txn.counterparty_id:
                continue
            amount = float(txn.amount) if txn.amount is not None else 0.0
            if amount >= 0:
                continue  # Only outgoing payments knock off AP

            try:
                abs_amount = abs(amount)

                # Only counterparties with OPEN invoices participate in AP knock-off.
                # No open invoices → not an AP payment; skip to Phase 4 (rules/default/AI).
                # (Case 3 asset-parking below must only fire when invoices DO exist but
                # none match the amount — otherwise every employee/vendor payment without
                # an invoice would wrongly park to 1300.)
                if not invoice_service.get_open_for_match(
                    db, txn.counterparty_id, txn.currency, txn.transaction_date
                ):
                    continue

                # Deterministic 3-case match (NOT AI): Case 1 reference + amount,
                # Case 2 FIFO amount, Case 3 → None. Returns the invoice to knock off.
                matched_invoice = invoice_service.get_open_for_counterparty(
                    db,
                    txn.counterparty_id,
                    abs_amount,
                    txn.currency,
                    description=txn.description or "",
                    reference_number=txn.reference_number or "",
                    transaction_date=txn.transaction_date,
                )

                if matched_invoice is not None:
                    # CASE 1/2 — knock off via the canonical path: match_transaction creates
                    # Dr 2000 AP / Cr Bank (or paired IC JEs cross-entity), records the
                    # payment, and marks the txn MATCHED. Invoice COA wins over the default.
                    res = invoice_service.match_transaction(
                        db, matched_invoice.id, txn.id, matched_by="ap_knockoff"
                    )
                    txn.coa_account_code = matched_invoice.contra_account_code
                    txn.categorization_type = CategorizationType.EXPENSE
                    txn.categorized_by_logic = 'invoice_knockoff'
                    db.commit()

                    results.append({
                        "transaction_id": txn.id,
                        "status": "categorized",
                        "rule_name": f"[ap_knockoff:invoice_{matched_invoice.id}]",
                        "journal_entry_id": res["journal_entry_id"],
                        "cross_entity": res["cross_entity"],
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
                        txn.categorized_by_logic = 'asset_parking'
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
                # Unexpected failure (the "no open invoices" / "no match" paths are
                # normal control flow, not exceptions) — log at ERROR so code bugs
                # surface instead of hiding as warnings (cf. BUG-1). Batch proceeds.
                logger.error(
                    f"Unexpected AP knock-off error for transaction {txn.id}: {e}",
                    exc_info=True,
                )
                db.rollback()
                # Do not add to handled — let Phase 4 handle it normally

        return handled

    # ------------------------------------------------------------------
    # Phase 3.5: Employee-claim Knock-off (POL-139 cat 4)
    # ------------------------------------------------------------------

    def _try_claim_knockoff(self, db, transactions, results) -> set[int]:
        """Settle approved employee claims from matching outgoing reimbursements. Same shape as the
        payroll knock-off: for each outgoing txn, find an APPROVED, not-yet-paid claim in the SAME entity
        whose amount matches (exact, tol 0.01) within a ±7-day window; post Dr 2303 / Cr bank via
        claim_service and mark the claim PAID. Conservative (exact amount + same entity) to avoid
        mis-settling — the categorization engine stays the sole matcher."""
        from datetime import timedelta
        from src.models.employee_claim import FinanceEmployeeClaim, ClaimStatus
        from src.services.claim_service import claim_service
        handled: set[int] = set()
        outgoing = [t for t in transactions if t.amount is not None and float(t.amount) < 0
                    and t.status == TransactionStatus.PENDING]
        if not outgoing:
            return handled
        ba_ids = {t.bank_account_id for t in outgoing}
        ba_map = {ba.id: ba for ba in db.query(FinanceBankAccount)
                  .filter(FinanceBankAccount.id.in_(ba_ids)).all()}
        for txn in outgoing:
            ba = ba_map.get(txn.bank_account_id)
            if not ba or not ba.entity_id:
                continue
            abs_amount = abs(float(txn.amount))
            # A claim is reimbursed AFTER approval, often weeks later — so window on "approved on/before
            # the payment (+1d grace), within the last 180d", not a tight ±7d around approval. Oldest first.
            lo = txn.transaction_date - timedelta(days=180)
            hi = txn.transaction_date + timedelta(days=1)
            # Amount fallback fires ONLY for claims marked paid-OUTSIDE the system (RECONCILE). System-paid
            # claims settle deterministically via Rung 1 (transfer-id on their payout). The reconcile guard
            # stops a random payment from auto-settling an approved-but-not-yet-paid claim.
            claim = (db.query(FinanceEmployeeClaim)
                     .filter(FinanceEmployeeClaim.status == ClaimStatus.RECONCILE.value,
                             FinanceEmployeeClaim.entity_id == ba.entity_id,
                             FinanceEmployeeClaim.transaction_id.is_(None),
                             FinanceEmployeeClaim.approved_at.between(lo, hi))
                     .order_by(FinanceEmployeeClaim.approved_at.asc(), FinanceEmployeeClaim.id.asc()).all())
            match = next((c for c in claim if abs(float(c.amount) - abs_amount) <= 0.01), None)
            if not match:
                continue
            je = claim_service.create_claim_payment_entries(
                db=db, bank_account=ba, claim=match, txn_date=txn.transaction_date,
                abs_amount=abs_amount, source="claim_knockoff",
                description=f"Reimburse employee claim #{match.id}")
            txn.status = TransactionStatus.MATCHED
            txn.matched_at = datetime.now(UTC)
            txn.categorized_by_logic = "claim_knockoff"
            txn.reconciled_journal_entry_id = je.id
            match.transaction_id = txn.id
            db.commit()
            results.append({"transaction_id": txn.id, "status": "categorized",
                            "rule_name": f"[claim_knockoff:claim_{match.id}]"})
            handled.add(txn.id)
        return handled

    # ------------------------------------------------------------------
    # Phase 3.6: Payroll register-payout Knock-off (PR-4b)
    # ------------------------------------------------------------------

    def _try_payroll_register_knockoff(self, db, transactions, results) -> set[int]:
        """Settle fanned-out payroll payables (payable_type='payroll') from matching outgoing payments.
        The liability to clear is 2304 (net salary) or, for a statutory payout, the code in
        `external_reference='statutory:<code>'`. Posts Dr <liability> / Cr bank, marks the payout POSTED.
        Same shape as the claim knock-off; exact amount + same entity."""
        from src.models.vendor_payout import FinancePayout, PayoutState
        from src.services.journal_service import journal_service
        from src.models.journal_entry import JournalEntryStatus
        handled: set[int] = set()
        outgoing = [t for t in transactions if t.amount is not None and float(t.amount) < 0
                    and t.status == TransactionStatus.PENDING]
        if not outgoing:
            return handled
        ba_map = {ba.id: ba for ba in db.query(FinanceBankAccount)
                  .filter(FinanceBankAccount.id.in_({t.bank_account_id for t in outgoing})).all()}
        for txn in outgoing:
            ba = ba_map.get(txn.bank_account_id)
            if not ba or not ba.entity_id or not ba.coa_account_code:
                continue
            abs_amount = abs(float(txn.amount))
            # Amount fallback fires ONLY for payouts marked paid-OUTSIDE the system (RECONCILE). System-paid
            # payroll settles via Rung 1 (transfer-id). The reconcile guard prevents a stray payment from
            # auto-settling a payout that wasn't actually paid.
            payout = (db.query(FinancePayout)
                      .filter(FinancePayout.payable_type == "payroll",
                              FinancePayout.entity_id == ba.entity_id,
                              FinancePayout.transaction_id.is_(None),
                              FinancePayout.state == PayoutState.RECONCILE.value)
                      .order_by(FinancePayout.id.asc()).all())
            match = next((p for p in payout if abs(float(p.amount) - abs_amount) <= 0.01), None)
            if not match:
                continue
            # FX-aware settlement (item 2): reuse the same helper Rung 1 uses, so the register fallback
            # clears the accrued functional liability against the converted payment (residue → 7100)
            # instead of booking at fx=1. Same-ccy collapses to the prior behaviour.
            je_id = self._settle_payroll_payout(db, match, txn, ba)
            if je_id is None:
                continue
            match.state = PayoutState.POSTED.value
            match.transaction_id = txn.id
            match.journal_entry_id = je_id
            txn.status = TransactionStatus.MATCHED
            txn.matched_at = datetime.now(UTC)
            txn.categorized_by_logic = "payroll_register_knockoff"
            txn.reconciled_journal_entry_id = je_id
            db.commit()
            results.append({"transaction_id": txn.id, "status": "categorized",
                            "rule_name": f"[payroll_register_knockoff:payout_{match.id}]"})
            handled.add(txn.id)
        return handled

    # ------------------------------------------------------------------
    # Rung 1: Transfer-ID knock-off (unified, deterministic, FX-aware)
    # ------------------------------------------------------------------
    def _try_transfer_id_knockoff(self, db, transactions, results) -> set[int]:
        """Rung 1 of the ladder: a bank txn we paid THROUGH OUR SYSTEM carries a wise_transfer_id that
        matches an AWAITING_IMPORT payout in the register. Deterministic (no fuzzy match), currency-
        AGNOSTIC (matches on the id, so an SGD payable settles against a USD payment), and FX-aware.
        One rung for every payable type — invoice / claim / payroll — dispatched by payout.payable_type.
        Runs before the amount-based fallbacks so system-paid settlements never depend on amounts."""
        from src.models.vendor_payout import FinancePayout, PayoutState
        handled: set[int] = set()
        outgoing = [t for t in transactions
                    if t.amount is not None and float(t.amount) < 0
                    and t.status == TransactionStatus.PENDING
                    and getattr(t, "wise_transfer_id", None)]
        if not outgoing:
            return handled
        for txn in outgoing:
            tid = str(txn.wise_transfer_id)
            payout = (db.query(FinancePayout)
                      .filter(FinancePayout.wise_transfer_id == tid,
                              FinancePayout.state == PayoutState.AWAITING_IMPORT.value,
                              FinancePayout.transaction_id.is_(None))
                      .first())
            if not payout:
                continue
            try:
                je_id = self._settle_payout_by_type(db, payout, txn)
            except Exception as e:
                # ROLL BACK the partially-flushed JE (VR-1c): without this, the orphaned lines ride the
                # NEXT iteration's db.commit() as a stray POSTED entry.
                db.rollback()
                logger.error("transfer-id knock-off failed for txn %s / payout %s: %s", txn.id, payout.id, e)
                continue
            if je_id is None:
                continue
            payout.transaction_id = txn.id
            payout.journal_entry_id = je_id
            payout.state = PayoutState.POSTED.value
            if txn.status != TransactionStatus.MATCHED:
                txn.status = TransactionStatus.MATCHED
                txn.matched_at = datetime.now(UTC)
                txn.reconciled_journal_entry_id = je_id
            txn.categorized_by_logic = "transfer_id_knockoff"
            db.commit()
            results.append({"transaction_id": txn.id, "status": "categorized",
                            "rule_name": f"[transfer_id_knockoff:{payout.payable_type}_{payout.id}]",
                            "journal_entry_id": je_id})
            handled.add(txn.id)
        return handled

    # ------------------------------------------------------------------
    # Phase 3.7: Invoice register-payout Knock-off (paid-outside AP)
    # ------------------------------------------------------------------

    def _try_invoice_register_knockoff(self, db, transactions, results) -> set[int]:
        """Phase 3.7: settle a paid-OUTSIDE invoice payout (payable_type='invoice', state=RECONCILE, no
        wise_transfer_id) against the matching outgoing bank line. mark_paid_already captured the exact
        amount, so we amount-match and settle via the FX-aware AP path (_settle_payout_by_type → invoice
        match_transaction, whose status gate now admits RECONCILE). Guarded on RECONCILE so a stray
        payment can't auto-settle an invoice that wasn't marked paid-outside. Mirrors the payroll/claim
        register phases; fixes paid-outside invoices accumulating unmatched bank lines forever."""
        from src.models.vendor_payout import FinancePayout, PayoutState
        handled: set[int] = set()
        outgoing = [t for t in transactions if t.amount is not None and float(t.amount) < 0
                    and t.status == TransactionStatus.PENDING]
        if not outgoing:
            return handled
        ba_map = {ba.id: ba for ba in db.query(FinanceBankAccount)
                  .filter(FinanceBankAccount.id.in_({t.bank_account_id for t in outgoing})).all()}
        for txn in outgoing:
            ba = ba_map.get(txn.bank_account_id)
            if not ba or not ba.entity_id:
                continue
            abs_amount = abs(float(txn.amount))
            payouts = (db.query(FinancePayout)
                       .filter(FinancePayout.payable_type == "invoice",
                               FinancePayout.entity_id == ba.entity_id,
                               FinancePayout.transaction_id.is_(None),
                               FinancePayout.state == PayoutState.RECONCILE.value)
                       .order_by(FinancePayout.id.asc()).all())
            match = next((p for p in payouts if abs(float(p.amount) - abs_amount) <= 0.01), None)
            if not match:
                continue
            try:
                je_id = self._settle_payout_by_type(db, match, txn)
            except Exception as e:
                db.rollback()   # VR-1c: never let a partial JE ride the next commit
                logger.error("invoice register knock-off failed for txn %s / payout %s: %s",
                             txn.id, match.id, e)
                continue
            if je_id is None:
                continue
            match.state = PayoutState.POSTED.value
            match.transaction_id = txn.id
            match.journal_entry_id = je_id
            if txn.status != TransactionStatus.MATCHED:
                txn.status = TransactionStatus.MATCHED
                txn.matched_at = datetime.now(UTC)
                txn.reconciled_journal_entry_id = je_id
            txn.categorized_by_logic = "invoice_register_knockoff"
            db.commit()
            results.append({"transaction_id": txn.id, "status": "categorized",
                            "rule_name": f"[invoice_register_knockoff:payout_{match.id}]"})
            handled.add(txn.id)
        return handled

    def _settle_payout_by_type(self, db, payout, txn) -> Optional[int]:
        """Post the FX-aware settlement JE for a payout matched by transfer id, dispatched by
        payable_type. Returns the JE id. invoice → the existing FX-aware AP match; claim → the FX-aware
        claim reimbursement (F2); payroll → clear 2304/statutory (functional payable) vs the converted
        bank payment, residue → 7100."""
        ba = db.query(FinanceBankAccount).filter(FinanceBankAccount.id == txn.bank_account_id).first()
        if not ba or not ba.coa_account_code:
            return None
        ptype = payout.payable_type
        if ptype == "invoice":
            inv_id = payout.invoice_id or payout.payable_id
            from src.services.invoice_service import invoice_service
            res = invoice_service.match_transaction(db, inv_id, txn.id, matched_by="transfer_id")
            return res.get("journal_entry_id") if isinstance(res, dict) else txn.reconciled_journal_entry_id
        if ptype == "claim":
            from src.models.employee_claim import FinanceEmployeeClaim
            claim = db.get(FinanceEmployeeClaim, payout.payable_id)
            if not claim:
                return None
            from src.services.claim_service import claim_service
            je = claim_service.create_claim_payment_entries(
                db, ba, claim, txn.transaction_date, abs(float(txn.amount)),
                source="transfer_id_knockoff", description=f"Claim #{claim.id} settlement (transfer {payout.wise_transfer_id})")
            return je.id
        if ptype == "payroll":
            return self._settle_payroll_payout(db, payout, txn, ba)
        return None

    def _settle_payroll_payout(self, db, payout, txn, ba) -> Optional[int]:
        """FX-aware payroll settlement. The accrued liability (2304 net, or statutory:<code>) is cleared
        at the functional amount that was accrued (payout.amount native converted at the run date).

        SAME entity: Dr liability / Cr bank at the payment converted at pay date, spread -> 7100.
        CROSS entity (bank entity != payroll entity): two internally-balanced paired JEs, each in ITS
        functional currency (POL-141 independent booking) — payroll entity Dr liability / Cr IC-payable;
        bank entity Dr IC-receivable / Cr bank. The IC balances differ across currencies and are trued
        at IC reconciliation (no 7100 plug here)."""
        from decimal import Decimal as _D
        from src.models.entity import FinanceEntity
        from src.models.payroll import FinancePayrollRun
        from src.services.fx_service import fx_service
        from src.models.journal_entry import JournalEntryStatus
        run = db.get(FinancePayrollRun, payout.payable_id)
        accr_date = run.run_date if run else txn.transaction_date
        native_pay = _D(str(abs(float(txn.amount))))
        ref = payout.external_reference or ""
        liability = ref.split(":", 1)[1] if ref.startswith("statutory:") else "2304"
        payroll_entity_id = payout.entity_id
        bank_entity_id = ba.entity_id

        if payroll_entity_id == bank_entity_id or not payroll_entity_id:
            # Same entity — single JE, FX spread to 7100
            func = db.get(FinanceEntity, bank_entity_id).base_currency if bank_entity_id else None
            payable_func, _ = fx_service.to_functional_or_same(db, _D(str(payout.amount)), payout.currency, func, accr_date)
            bank_func, rate = fx_service.to_functional_or_same(db, native_pay, txn.currency, func, txn.transaction_date)
            lines = [
                {"account_code": liability, "debit_amount": float(payable_func), "credit_amount": 0.0,
                 "description": f"Payroll payout #{payout.id} settlement",
                 "currency": func, "native_amount": payable_func, "fx_rate": _D("1")},
                {"account_code": ba.coa_account_code, "debit_amount": 0.0, "credit_amount": float(bank_func),
                 "description": f"Payroll payout #{payout.id} settlement",
                 "currency": txn.currency, "native_amount": native_pay, "fx_rate": rate},
            ]
            residue = payable_func - bank_func
            if abs(residue) >= _D("0.01"):
                if residue > 0:
                    lines.append({"account_code": "7100", "debit_amount": 0.0, "credit_amount": float(residue),
                                  "description": f"FX gain on payroll payout #{payout.id}"})
                else:
                    lines.append({"account_code": "7100", "debit_amount": float(-residue), "credit_amount": 0.0,
                                  "description": f"FX loss on payroll payout #{payout.id}"})
            je = journal_service.create(db=db, entity_id=bank_entity_id, entry_date=txn.transaction_date,
                                        description=f"Payroll payout #{payout.id} settlement",
                                        lines=lines, status=JournalEntryStatus.POSTED)
            je.source = "transfer_id_knockoff"
            db.flush()
            return je.id

        # Cross entity — paired IC JEs, each in its own functional currency
        from src.services.invoice_service import invoice_service
        ic_codes = invoice_service._get_ic_codes(db, bank_entity_id, payroll_entity_id)
        if not ic_codes:
            raise ValueError(f"No IC codes for entity pair (bank {bank_entity_id}, payroll {payroll_entity_id})")
        ic_receivable, ic_payable = ic_codes
        pf = db.get(FinanceEntity, payroll_entity_id).base_currency
        bf = db.get(FinanceEntity, bank_entity_id).base_currency
        payable_pf, _pr = fx_service.to_functional_or_same(db, _D(str(payout.amount)), payout.currency, pf, accr_date)
        bank_bf, _br = fx_service.to_functional_or_same(db, native_pay, txn.currency, bf, txn.transaction_date)
        import uuid as _uuid
        grp = str(_uuid.uuid4())
        desc = f"Payroll payout #{payout.id} settlement (IC)"
        # Payroll entity: Dr liability / Cr IC-payable (in payroll func)
        pe = journal_service.create(db=db, entity_id=payroll_entity_id, entry_date=txn.transaction_date, description=desc,
            lines=[{"account_code": liability, "debit_amount": float(payable_pf), "credit_amount": 0.0, "description": desc,
                    "currency": pf, "native_amount": payable_pf, "fx_rate": _D("1")},
                   {"account_code": ic_payable, "debit_amount": 0.0, "credit_amount": float(payable_pf), "description": desc,
                    "currency": pf, "native_amount": payable_pf, "fx_rate": _D("1")}],
            status=JournalEntryStatus.POSTED)
        pe.source = "transfer_id_knockoff"; pe.intercompany_group_id = grp
        # Bank entity: Dr IC-receivable / Cr bank (in bank func)
        be = journal_service.create(db=db, entity_id=bank_entity_id, entry_date=txn.transaction_date, description=desc,
            lines=[{"account_code": ic_receivable, "debit_amount": float(bank_bf), "credit_amount": 0.0, "description": desc,
                    "currency": txn.currency, "native_amount": native_pay, "fx_rate": _br},
                   {"account_code": ba.coa_account_code, "debit_amount": 0.0, "credit_amount": float(bank_bf), "description": desc,
                    "currency": txn.currency, "native_amount": native_pay, "fx_rate": _br}],
            status=JournalEntryStatus.POSTED)
        be.source = "transfer_id_knockoff"; be.intercompany_group_id = grp
        db.flush()
        # link the txn to the bank-entity JE (that's where the cash moved)
        return be.id

    # ------------------------------------------------------------------
    # Manual knock-off (human-driven) — item 5
    # ------------------------------------------------------------------
    def manual_knockoff(self, db, txn_id: int, payout_id: int, actor: Optional[str] = None) -> dict:
        """Human-driven knock-off: an operator explicitly pairs a bank transaction to a payout
        (invoice / claim / payroll) and posts the SAME FX-aware settlement the automated ladder would.
        The override for cases the ladder can't match on its own — a payment made outside the system,
        a cross-currency amount that doesn't line up, or an ambiguous match. No transfer-id or state
        guard: the human is asserting the pairing. Idempotency + basic safety are still enforced."""
        from src.models.vendor_payout import FinancePayout, PayoutState
        from src.utils.errors import NotFoundError, BadRequestError
        txn = db.get(FinanceTransaction, txn_id)
        if not txn:
            raise NotFoundError(f"Transaction {txn_id} not found")
        if txn.status == TransactionStatus.MATCHED:
            raise BadRequestError(f"Transaction {txn_id} is already matched")
        if txn.amount is None or float(txn.amount) >= 0:
            raise BadRequestError("Manual knock-off only applies to an outgoing (negative) payment")
        payout = db.get(FinancePayout, payout_id)
        if not payout:
            raise NotFoundError(f"Payout {payout_id} not found")
        if payout.transaction_id is not None or payout.state == PayoutState.POSTED.value:
            raise BadRequestError(f"Payout {payout_id} is already settled")
        je_id = self._settle_payout_by_type(db, payout, txn)
        if je_id is None:
            raise BadRequestError(f"Could not settle payout {payout_id} (unsupported type or missing data)")
        payout.transaction_id = txn.id
        payout.journal_entry_id = je_id
        payout.state = PayoutState.POSTED.value
        if txn.status != TransactionStatus.MATCHED:
            txn.status = TransactionStatus.MATCHED
            txn.matched_at = datetime.now(UTC)
            txn.reconciled_journal_entry_id = je_id
        txn.categorized_by_logic = f"manual_knockoff:{actor or 'admin'}"
        db.commit()
        logger.info("Manual knock-off: txn %s ↔ payout %s (%s) by %s → JE %s",
                    txn.id, payout.id, payout.payable_type, actor, je_id)
        return {"transaction_id": txn.id, "payout_id": payout.id, "payable_type": payout.payable_type,
                "journal_entry_id": je_id, "status": "matched"}

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
                # PR-4b: a run that fanned out into register payouts settles via Phase 3.6 (per-payout),
                # NOT the legacy aggregate net/CPF slots — exclude those runs to avoid double-settlement.
                from src.models.vendor_payout import FinancePayout
                fanned = {r[0] for r in db.query(FinancePayout.payable_id)
                          .filter(FinancePayout.payable_type == "payroll").distinct().all()}
                runs = [r for r in db.query(FinancePayrollRun).filter(
                    FinancePayrollRun.status == "POSTED",
                    FinancePayrollRun.run_date.between(date_low, date_high),
                ).all() if r.id not in fanned]
                # Prefer the transaction's own entity: a same-entity payroll run
                # should win over a coincidental amount match in another entity.
                # Cross-entity is still supported (below), but only when no
                # same-entity run matches.
                runs.sort(key=lambda r: 0 if r.entity_id == entity_id else 1)

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
                # Unexpected failure (the "no match" path is a normal `continue`,
                # not an exception) — log at ERROR so code bugs surface instead of
                # hiding as warnings (cf. BUG-1). The batch still proceeds.
                logger.error(
                    f"Unexpected payroll knock-off error for transaction {txn.id}: {e}",
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

        # Load the full party directory once for the whole batch.
        # INACTIVE parties are included deliberately: inactive means DORMANT
        # (a vendor we no longer deal with), and historical transactions must
        # still enrich against them. Records that are WRONG are deleted from
        # the table, never kept inactive (Gaurav, 2026-07-25 — POL-22).
        counterparties = db.query(FinanceCounterparty).all()
        if not counterparties:
            return

        unmatched: list[FinanceTransaction] = []

        for txn in transactions:
            if txn.counterparty_id:
                continue  # already linked

            matched, ambiguous = self._match_l1(txn, counterparties)
            if ambiguous:
                # >1 counterparty ties at L1's STRONGEST tier — never guess (kills the
                # old first-match-wins misattribution); let the L3 LLM decide.
                unmatched.append(txn)
                continue
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
    ) -> tuple[Optional[Any], bool]:
        """
        L1: deterministic TIERED matching against name and aliases.

        Match strength (strongest first), so a weak signal never beats a strong one:
          Tier 1 — exact: raw counterparty_name == name/alias
          Tier 2 — name/alias is a substring of the raw counterparty_name
          Tier 3 — name/alias is a substring of the DESCRIPTION (weakest — bank
                   statement footers/disclaimers live here, e.g. the SDIC "…CPF
                   Investment Scheme…" boilerplate that falsely hits party "CPF")

        Returns (matched_cp, ambiguous):
          - unique best-tier match  → (cp, False)
          - ≥2 parties tie at the best tier → (None, True)  ← caller routes to the LLM
          - no match at all           → (None, False)
        This kills the old first-match-wins misattribution: an exact counterparty_name
        hit (Tier 1) always wins over a description-footer substring (Tier 3).

        Short names/aliases (< 6 chars) match on WORD BOUNDARIES, not raw substring —
        party "URA" must never match "InsURAnce" (2026-07-25 bug; trap class DQ-13).
        """
        raw_cp = (txn.counterparty_name or "").lower().strip()
        raw_desc = (txn.description or "").lower().strip()

        def contains(needle: str, haystack: str) -> bool:
            if not haystack:
                return False
            if len(needle) >= 6:
                return needle in haystack
            return re.search(r"\b" + re.escape(needle) + r"\b", haystack) is not None

        def cp_tier(cp) -> Optional[int]:
            """Best (lowest) tier this counterparty achieves for this txn, or None."""
            best = None
            for term in ([cp.name] + list(cp.aliases or [])):
                if not isinstance(term, str):
                    continue
                t = term.lower().strip()
                if not t:
                    continue
                if raw_cp and raw_cp == t:
                    return 1  # can't beat exact
                if raw_cp and contains(t, raw_cp):
                    best = min(best or 9, 2)
                elif contains(t, raw_desc):
                    best = min(best or 9, 3)
            return best

        best_tier = 99
        winners: list = []
        for cp in counterparties:
            if not (cp.name or "").strip():
                continue
            tier = cp_tier(cp)
            if tier is None:
                continue
            if tier < best_tier:
                best_tier, winners = tier, [cp]
            elif tier == best_tier and all(w.id != cp.id for w in winners):
                winners.append(cp)

        if not winners:
            return None, False
        if len(winners) == 1:
            return winners[0], False
        return None, True  # tie at the strongest tier → ambiguous → LLM decides

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

        try:
            client = anthropic.Anthropic(api_key=api_key)
            mapping: dict = {}
            CHUNK = 50  # keeps each JSON response comfortably under the token cap
            for start in range(0, len(unmatched), CHUNK):
                chunk = unmatched[start:start + CHUNK]
                txn_list = "\n".join(
                    f"  {txn.id}: desc=\"{txn.description}\" cp_field=\"{txn.counterparty_name or ''}\""
                    for txn in chunk
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

                message = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw_response = cast("TextBlock", message.content[0]).text.strip()

                # Strip markdown code fences if present
                if raw_response.startswith("```"):
                    raw_response = raw_response.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

                mapping.update(json_lib.loads(raw_response))

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

    @staticmethod
    def _default_direction_ok(counterparty: Any, transaction: FinanceTransaction) -> bool:
        """POL-34: a counterparty default fires only when the money moves in the
        counterparty-type's normal direction. Vendor/employee/government money
        normally goes OUT (we pay them); investor money comes IN (they pay us).
        A mismatch is a refund / repayment / claw-back — an exception that must
        reach review, not auto-book against the default. Unknown types and
        types with no clear direction (bank) always pass."""
        normal = CP_TYPE_NORMAL_DIRECTION.get(
            getattr(counterparty, "type", None), None)
        if normal is None:
            return True
        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        if amount == 0:
            return True
        actual = "out" if amount < 0 else "in"
        if actual == normal:
            return True
        logger.info(
            f"cp-direction-guard: txn {transaction.id} ({actual}, {amount}) contradicts "
            f"{counterparty.type} normal direction ({normal}) — skipping default "
            f"{counterparty.default_account_code}, routing to review")
        return False

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
        transaction.categorized_by_logic = 'counterparty_default'

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

        if (rule.category == TransactionCategory.INTERNAL_TRANSFER
                and rule.target_bank_account_id is None):
            # CLAIM-ONLY transfer rule (two-rules-per-corridor law, Gaurav
            # 2026-07-25): this side of the corridor cannot know its counterpart
            # (e.g. a Wise top-up can't tell WHICH bank funded it), so it books
            # NO JE — it just claims the transaction as a transfer so enrichment
            # and AI never touch it, and waits for the knowing side's counter-
            # search to attach it to that side's JE.
            transaction.categorization_type = CategorizationType.INTERNAL_TRANSFER
            transaction.status = TransactionStatus.AWAITING_MATCH
            transaction.expected_counterpart_ba_id = None
            transaction.categorized_by_rule_id = rule.id
            transaction.categorized_by_logic = 'transfer_rule'
            self._apply_tags(db, transaction.id, rule.tag_ids)
            db.commit()
            logger.info(
                f"Transfer claim-only: txn {transaction.id} claimed by rule {rule.id} "
                f"(no target) — awaiting the knowing side's JE"
            )
            return {
                "transaction_id": transaction.id,
                "status": "awaiting_match",
                "rule_name": rule.name,
                "journal_entry_id": None,
                "error": None,
            }

        if rule.category == TransactionCategory.INTERNAL_TRANSFER:
            # Both-sides-know guard: if the movement is ALREADY booked by an
            # awaiting leg on the counterpart account (corridors where both
            # directions carry a knowing rule, e.g. C1 #2/#26), attach to that
            # JE instead of writing a second one for the same cash.
            if rule.target_bank_account_id:
                mirror = self._find_awaiting_mirror_je(
                    db, transaction, rule.target_bank_account_id)
                if mirror:
                    now = datetime.now(UTC)
                    je_id = mirror.reconciled_journal_entry_id
                    for leg in (transaction, mirror):
                        leg.status = TransactionStatus.MATCHED
                        leg.matched_at = now
                        leg.expected_counterpart_ba_id = None
                    transaction.reconciled_journal_entry_id = je_id
                    transaction.categorization_type = CategorizationType.INTERNAL_TRANSFER
                    transaction.categorized_by_rule_id = rule.id
                    transaction.categorized_by_logic = 'transfer_pairing'
                    self._apply_tags(db, transaction.id, rule.tag_ids)
                    db.commit()
                    logger.info(
                        f"Transfer attached to mirror JE {je_id}: txn {transaction.id} "
                        f"↔ awaiting txn {mirror.id} (both-sides-know corridor)")
                    return {
                        "transaction_id": transaction.id,
                        "status": "categorized",
                        "rule_name": f"{rule.name} [attached to mirror JE {je_id}]",
                        "journal_entry_id": je_id,
                        "error": None,
                    }
            # POL-141/142: a CROSS-CURRENCY intra-entity transfer (source ccy != target ccy) can't be
            # booked from one leg — the two legs have genuinely different amounts and the FX residual
            # needs both. DEFER the JE (journal_entry=None) and build the FX-plug entry at pairing.
            _tgt_ccy, _tgt_ba = self._bank_ccy(db, rule.target_bank_account_id) if rule.target_bank_account_id else (None, None)
            _src_ccy = bank_account.currency if bank_account else None
            _cross_entity = bool(_tgt_ba and bank_account and _tgt_ba.entity_id != bank_account.entity_id)
            if _cross_entity:
                # IC1 — independent booking. The moment we see an intercompany transfer we book THIS
                # entity's own leg straight away, converted to its functional currency. We do NOT wait for
                # or pair the other side: that entity books its own leg from its own bank feed. The two IC
                # balances differ across currencies and are trued at IC reconciliation.
                journal_entry = self._create_internal_transfer_entries(
                    db, transaction, rule, bank_account, amount, abs_amount)
            elif _src_ccy and _tgt_ccy and _src_ccy != _tgt_ccy:
                journal_entry = None   # cross-ccy INTRA → defer, FX-plug built at pairing (POL-144)
            else:
                journal_entry = self._create_internal_transfer_entries(
                    db, transaction, rule, bank_account, amount, abs_amount)
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

        # POL-12: rules never assign counterparties — identity belongs to enrichment
        # (names + aliases). rule.counterparty_name/type are legacy action fields,
        # deliberately ignored here; counterparty_* CONDITIONS remain supported.

        # Set COA account code for non-internal-transfer categorizations
        if rule.category != TransactionCategory.INTERNAL_TRANSFER:
            transaction.coa_account_code = contra_account_code

        # Set categorization type from rule category
        category_map = {
            TransactionCategory.EXPENSE: CategorizationType.EXPENSE,
            TransactionCategory.DEPOSIT: CategorizationType.DEPOSIT,
            TransactionCategory.INTERNAL_TRANSFER: CategorizationType.INTERNAL_TRANSFER,
            TransactionCategory.CROSS_ENTITY_ALLOCATION: CategorizationType.EXPENSE,  # Cross-entity is a specialized expense
            TransactionCategory.INTERCOMPANY_TRANSFER: CategorizationType.INTERCOMPANY,
        }
        if rule.category in category_map:
            transaction.categorization_type = category_map[rule.category]

        # Route audit — stamped for every rule outcome below (MATCHED, paired,
        # awaiting); transfer rules get their own label so the FE can say
        # "recognized as internal transfer" rather than a generic rule hit.
        transaction.categorized_by_rule_id = rule.id
        transaction.categorized_by_logic = (
            'transfer_rule' if rule.category == TransactionCategory.INTERNAL_TRANSFER
            else 'ic_rule' if rule.category == TransactionCategory.INTERCOMPANY_TRANSFER
            else 'rule'
        )

        # IC1 — a cross-entity (intercompany) transfer books ONE leg independently and is DONE. No
        # pairing, no AWAITING_MATCH: the other entity records its own leg from its own feed. This is the
        # "book it straight away the moment we see it's intercompany" path.
        if (rule.category == TransactionCategory.INTERNAL_TRANSFER and _cross_entity
                and journal_entry is not None):
            transaction.status = TransactionStatus.MATCHED
            transaction.reconciled_journal_entry_id = journal_entry.id
            transaction.matched_at = datetime.now(UTC)
            transaction.categorized_by_logic = 'intercompany_independent'
            db.commit()
            return {"transaction_id": transaction.id, "status": "categorized",
                    "rule_name": f"{rule.name} [intercompany leg booked independently]",
                    "journal_entry_id": journal_entry.id, "error": None}

        # For internal transfers: try to immediately pair with counter-transaction.
        # If counter not found yet → AWAITING_MATCH; the counter-transaction will
        # complete the pair when it arrives and Step 0 runs next time.
        # EXCEPTION: targets with NO statement feed (Stripe Connect — per-booking
        # micro-payouts we deliberately never import, Gaurav 2026-07-25) complete
        # as MATCHED standalone: the JE fully books the movement and the second
        # statement line does not exist by design. The Connect ledger balance is
        # verified in AGGREGATE at the Stripe-sync tie-out instead.
        if (rule.category == TransactionCategory.INTERNAL_TRANSFER
                and rule.target_bank_account_id
                and journal_entry is not None  # cross-ccy defers the JE — can't standalone-match on one leg
                and self._target_has_no_statement_feed(db, rule.target_bank_account_id)):
            transaction.status = TransactionStatus.MATCHED
            transaction.reconciled_journal_entry_id = journal_entry.id
            transaction.matched_at = datetime.now(UTC)
            logger.info(
                f"Internal transfer standalone-matched: txn {transaction.id} — "
                f"target ba={rule.target_bank_account_id} has no statement feed"
            )
        elif rule.category == TransactionCategory.INTERNAL_TRANSFER and rule.target_bank_account_id:
            counter_txn = self._find_counter_transaction(
                db, transaction, rule.target_bank_account_id
            )
            if counter_txn:
                # Pair both sides right now. Cross-ccy (journal_entry is None) → build the FX-plug JE from
                # BOTH legs now (both amounts known); same-ccy → both share the leg-1 JE.
                now = datetime.now(UTC)
                if journal_entry is None:
                    out_leg, in_leg = ((transaction, counter_txn) if float(transaction.amount) < 0
                                       else (counter_txn, transaction))
                    journal_entry = self._create_fx_transfer_je(db, out_leg, in_leg)
                    counter_txn.categorized_by_logic = 'transfer_pairing_fx'
                else:
                    counter_txn.categorized_by_logic = 'transfer_pairing'
                transaction.status = TransactionStatus.MATCHED
                transaction.reconciled_journal_entry_id = journal_entry.id
                transaction.matched_at = now
                counter_txn.status = TransactionStatus.MATCHED
                counter_txn.reconciled_journal_entry_id = journal_entry.id
                counter_txn.matched_at = now
                counter_txn.categorization_type = CategorizationType.INTERNAL_TRANSFER
                logger.info(
                    f"Internal transfer paired: txn {transaction.id} ↔ txn {counter_txn.id} "
                    f"via JE {journal_entry.id}"
                )
            else:
                # Counter not yet imported — wait. Cross-ccy leaves reconciled_journal_entry_id NULL
                # (deferred); the FX-plug JE is minted when the counter arrives (Phase 0).
                transaction.status = TransactionStatus.AWAITING_MATCH
                transaction.reconciled_journal_entry_id = journal_entry.id if journal_entry else None
                transaction.expected_counterpart_ba_id = rule.target_bank_account_id
                logger.info(
                    f"Internal transfer awaiting counter: txn {transaction.id} "
                    f"waiting for ba={rule.target_bank_account_id} (xccy={journal_entry is None})"
                )
        else:
            # Normal expense/deposit → MATCHED immediately
            transaction.status = TransactionStatus.MATCHED
            transaction.reconciled_journal_entry_id = journal_entry.id
            transaction.matched_at = datetime.now(UTC)

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

    def _target_has_no_statement_feed(self, db: Session, target_ba_id: int) -> bool:
        """True for transfer targets whose statements we deliberately never import.

        Stripe CONNECT balances: thousands of per-booking micro-payouts, verified
        in aggregate at the Stripe-sync tie-out instead (Gaurav, 2026-07-25).
        Transfers into these targets complete as MATCHED standalone — the second
        statement line does not exist by design.
        """
        ba = db.get(FinanceBankAccount, target_ba_id)
        return bool(ba and ba.bank_name == "Stripe"
                    and "connect" in (ba.account_name or "").lower())

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
        # SELF-JE GUARD (Gaurav, 2026-07-26): contra == the bank's own account
        # would book Dr X / Cr X — a no-op entry that LOOKS matched. The AI has
        # produced this twice (Wise top-up → 1001, Stripe AU payouts → 1019);
        # such movements are internal transfers and belong to the transfer lane.
        if contra_code == bank_coa_code:
            raise ValueError(
                f"Refusing self-referencing journal entry: contra account {contra_code} "
                f"IS the bank account's own COA code. This transaction is almost "
                f"certainly an internal transfer — categorize it via a transfer rule.")

        je_description = description_override or transaction.description or "Categorized transaction"

        # POL-25: the ledger books in the entity's FUNCTIONAL currency, converted
        # at booking time (monthly standard rate, POL-26); the native statement
        # amount + rate survive on every line. Same-currency txns pass through
        # at rate 1 unchanged.
        from src.models.entity import FinanceEntity
        from src.services.fx_service import fx_service
        entity_row = db.get(FinanceEntity, entity_id)
        functional_ccy = entity_row.base_currency if entity_row else None
        native_ccy = transaction.currency or functional_ccy
        fx_rate = Decimal("1")
        if functional_ccy and native_ccy != functional_ccy:
            functional_abs, fx_rate = fx_service.to_functional(
                db, Decimal(str(abs_amount)), native_ccy, functional_ccy,
                transaction.transaction_date)
            abs_amount = float(functional_abs)

        # PR-1: GST at the draft-JE machine now runs through the ONE locked decision
        # (gst_service.classify), not the old account.gst_applicable boolean. This is a cash
        # leg (the bank txn), so it recognises input→1350 / output→2500 and — critically —
        # applies the vendor gate on input (DQ-99): no counterparty or an unregistered vendor
        # yields REVIEW, i.e. NO auto-claim, instead of the old over-claim on the COA flag alone.
        gst_account, gst_amount = self._resolve_gst(
            db, entity_id=entity_id, contra_code=contra_code,
            counterparty_id=transaction.counterparty_id, abs_amount=abs_amount,
            direction=("input" if amount < 0 else "output"), rule=rule, gst_override=gst_override,
        )

        if gst_account and gst_amount > 0:
            ex_gst = round(abs_amount - gst_amount, 2)
            if amount < 0:
                lines = [
                    {"account_code": contra_code, "debit_amount": ex_gst,    "credit_amount": 0.0,       "description": je_description},
                    {"account_code": gst_account, "debit_amount": gst_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": bank_coa_code, "debit_amount": 0.0,      "credit_amount": abs_amount, "description": je_description},
                ]
            else:
                lines = [
                    {"account_code": bank_coa_code, "debit_amount": abs_amount, "credit_amount": 0.0,       "description": je_description},
                    {"account_code": contra_code,   "debit_amount": 0.0,        "credit_amount": ex_gst,    "description": je_description},
                    {"account_code": gst_account,   "debit_amount": 0.0,        "credit_amount": gst_amount, "description": je_description},
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

        # Stamp the currency facts on every line (native derived per line so
        # GST splits keep proportional native amounts).
        full_native = Decimal(str(transaction.amount)).copy_abs() if transaction.amount is not None else None
        for l in lines:
            func_amt = Decimal(str(l["debit_amount"] if l["debit_amount"] else l["credit_amount"]))
            l["currency"] = native_ccy
            l["fx_rate"] = fx_rate
            if fx_rate == 1:
                l["native_amount"] = func_amt
            elif full_native is not None and func_amt == Decimal(str(abs_amount)):
                # full-amount lines carry the txn's TRUE native (never re-derive
                # by division — rounding drift breaks native-basis recon)
                l["native_amount"] = full_native
            else:
                l["native_amount"] = (func_amt / fx_rate).quantize(Decimal("0.01"))

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

    def _to_func(self, db, amount_abs, ccy, functional_ccy, on_date):
        """(functional_amount, rate). Same-ccy → (amount, 1); else fx_service.to_functional (POL-26)."""
        from decimal import Decimal
        from src.services.fx_service import fx_service
        amt = Decimal(str(amount_abs))
        if not functional_ccy or (ccy or functional_ccy) == functional_ccy:
            return amt, Decimal("1")
        return fx_service.to_functional(db, amt, ccy, functional_ccy, on_date)

    def _bank_ccy(self, db, ba_id):
        ba = db.query(FinanceBankAccount).filter(FinanceBankAccount.id == ba_id).first()
        return (ba.currency if ba else None), ba

    def _create_fx_transfer_je(self, db, leg_out, leg_in):
        """POL-141/142: cross-currency intra-entity internal transfer. Both legs are known (paired), each
        converts INDEPENDENTLY to the entity's functional currency; the residual plugs to 7100 FX — the
        invoice pattern applied to two asset accounts. `leg_out` sent (negative), `leg_in` received
        (positive). Dr received-account (functional) / Cr sent-account (functional) / 7100 plug."""
        from decimal import Decimal
        from src.models.entity import FinanceEntity
        ba_out = db.query(FinanceBankAccount).filter(FinanceBankAccount.id == leg_out.bank_account_id).first()
        ba_in = db.query(FinanceBankAccount).filter(FinanceBankAccount.id == leg_in.bank_account_id).first()
        entity_id = ba_out.entity_id
        func = (db.get(FinanceEntity, entity_id).base_currency) if entity_id else None
        out_func, out_rate = self._to_func(db, abs(float(leg_out.amount)), leg_out.currency, func, leg_out.transaction_date)
        in_func, in_rate = self._to_func(db, abs(float(leg_in.amount)), leg_in.currency, func, leg_in.transaction_date)
        desc = leg_in.description or leg_out.description or "Internal transfer (FX)"
        lines = [
            {"account_code": ba_in.coa_account_code, "debit_amount": float(in_func), "credit_amount": 0.0,
             "description": desc, "currency": leg_in.currency, "native_amount": abs(float(leg_in.amount)), "fx_rate": in_rate},
            {"account_code": ba_out.coa_account_code, "debit_amount": 0.0, "credit_amount": float(out_func),
             "description": desc, "currency": leg_out.currency, "native_amount": abs(float(leg_out.amount)), "fx_rate": out_rate},
        ]
        diff = in_func - out_func  # Dr side − Cr side
        if abs(diff) >= Decimal("0.01"):
            if diff > 0:   # more Dr than Cr → FX GAIN (extra credit)
                lines.append({"account_code": "7100", "debit_amount": 0.0, "credit_amount": float(diff), "description": "FX gain on internal transfer"})
            else:          # more Cr than Dr → FX LOSS (extra debit)
                lines.append({"account_code": "7100", "debit_amount": float(-diff), "credit_amount": 0.0, "description": "FX loss on internal transfer"})
        from src.models.journal_entry import JournalEntryStatus
        je = journal_service.create(db=db, entity_id=entity_id, entry_date=leg_in.transaction_date,
                                    description=desc, lines=lines, status=JournalEntryStatus.POSTED)
        je.source = "categorization_engine"
        db.flush()
        return je

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
        Different entities (intercompany): paired JEs with a shared intercompany_group_id,
        using a receivable/payable IC PAIR resolved per entity-pair (via
        invoice_service._get_ic_codes) — same convention as the allocation/AP paths, so
        each entity's standalone statements are correct and consolidation eliminates by pair.
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
            # IC1 — INDEPENDENT booking (POL-141). Book ONLY THIS entity's (the source's) leg, converted
            # to its functional currency, and post it straight away. The OTHER entity records its own leg
            # from its own bank feed — no paired target JE here. The two IC balances differ across
            # currencies and are trued at IC reconciliation.
            from src.services.invoice_service import invoice_service
            from src.models.entity import FinanceEntity
            from src.services.fx_service import fx_service
            from decimal import Decimal as _D
            _func = db.get(FinanceEntity, source_entity_id).base_currency if source_entity_id else None
            _native = _D(str(abs_amount))
            _amt, _rate = fx_service.to_functional_or_same(
                db, _native, transaction.currency, _func, transaction.transaction_date)
            _meta = {"currency": transaction.currency, "native_amount": _native, "fx_rate": _rate}
            if amount < 0:
                # Source pays out → source FUNDS the other entity → source holds the RECEIVABLE
                ic_codes = invoice_service._get_ic_codes(db, source_entity_id, target_entity_id)
                if not ic_codes:
                    raise ValueError(f"No intercompany codes for entity pair ({source_entity_id} → {target_entity_id}).")
                ic_receivable, _ = ic_codes
                lines = [
                    {"account_code": ic_receivable, "debit_amount": float(_amt), "credit_amount": 0.0,        "description": je_description, **_meta},
                    {"account_code": source_coa,    "debit_amount": 0.0,         "credit_amount": float(_amt), "description": je_description, **_meta},
                ]
            else:
                # Source receives in → the other entity FUNDED source → source holds the PAYABLE
                ic_codes = invoice_service._get_ic_codes(db, target_entity_id, source_entity_id)
                if not ic_codes:
                    raise ValueError(f"No intercompany codes for entity pair ({target_entity_id} → {source_entity_id}).")
                _, ic_payable = ic_codes
                lines = [
                    {"account_code": source_coa, "debit_amount": float(_amt), "credit_amount": 0.0,        "description": je_description, **_meta},
                    {"account_code": ic_payable, "debit_amount": 0.0,         "credit_amount": float(_amt), "description": je_description, **_meta},
                ]
            entry = journal_service.create(
                db=db, entity_id=source_entity_id, entry_date=transaction.transaction_date,
                description=je_description, lines=lines)
            entry.source = "categorization_engine"
            db.flush()
            return entry

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
        from src.services.fx_service import fx_service

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

        # POL-141: each entity books its OWN leg in ITS OWN functional currency (independent booking).
        # The cash amount is native in the transaction's currency; convert per entity at the txn date.
        # IC receivable (bank func) and IC payable (alloc func) differ across currencies — that spread
        # is an intercompany FX item trued at IC reconciliation, not a per-txn plug.
        from decimal import Decimal as _D
        _txn_ccy = transaction.currency
        _native = _D(str(abs_amount))
        _bank_amt, _bank_rate = fx_service.to_functional_or_same(db, _native, _txn_ccy, bank_entity.base_currency, transaction.transaction_date)
        _alloc_amt, _alloc_rate = fx_service.to_functional_or_same(db, _native, _txn_ccy, alloc_entity.base_currency, transaction.transaction_date)
        _bank_meta = {"currency": _txn_ccy, "native_amount": _native, "fx_rate": _bank_rate}
        _alloc_meta = {"currency": _txn_ccy, "native_amount": _native, "fx_rate": _alloc_rate}

        # Bank entity: pays out cash → Dr IC Receivable (asset: they are owed by alloc entity)
        #                             Cr Bank
        bank_lines = [
            {"account_code": ic_recv_code,               "debit_amount": float(_bank_amt), "credit_amount": 0.0,        "description": je_description, **_bank_meta},
            {"account_code": bank_account.coa_account_code, "debit_amount": 0.0,      "credit_amount": float(_bank_amt), "description": je_description, **_bank_meta},
        ]

        # Allocation entity: bears the cost → Dr Expense (contra_account_code)
        #                                      Cr IC Payable (they owe the bank entity)
        alloc_lines = [
            {"account_code": rule.contra_account_code, "debit_amount": float(_alloc_amt), "credit_amount": 0.0,        "description": je_description, **_alloc_meta},
            {"account_code": ic_pay_code,              "debit_amount": 0.0,        "credit_amount": float(_alloc_amt), "description": je_description, **_alloc_meta},
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

    def _resolve_gst(
        self,
        db: Session,
        *,
        entity_id: int,
        contra_code: str,
        counterparty_id: Optional[int],
        abs_amount: float,
        direction: str,
        rule: Optional[FinanceCategorizationRule] = None,
        gst_override: Optional[bool] = None,
    ) -> tuple[Optional[str], float]:
        """PR-1: the ONE going-forward GST decision for a matched bank txn (a cash leg).

        Returns (gst_account_code, gst_amount) or (None, 0.0) for no GST. An explicit or rule-level
        override still forces the answer (operator intent wins); otherwise gst_service.classify() —
        the locked model — decides, including the input vendor gate (DQ-99). A REVIEW verdict means
        NO auto-claim: we book the plain 2-line entry and leave the claim for substantiation.
        """
        from src.services import gst_service

        # Operator/rule override forces the outcome; only meaningful when the entity is registered.
        forced: Optional[bool] = gst_override
        if forced is None and rule is not None and rule.gst_override is not None:
            forced = rule.gst_override
        if forced is False:
            return None, 0.0
        if forced is True:
            if not gst_service.entity_is_gst_registered(db, entity_id):
                return None, 0.0
            amt = gst_service.gst_from_gross(abs_amount)
            code = gst_service.GST_INPUT if direction == "input" else gst_service.GST_OUTPUT
            return (code, amt) if amt > 0 else (None, 0.0)

        market = gst_service.market_for_entity(entity_id)
        vendor_flag = (
            gst_service.vendor_registered(db, counterparty_id, market)
            if (direction == "input" and counterparty_id) else None
        )
        # POL-123 bank lane: the contra flag + the vendor gate are the WHOLE decision. No refund
        # marker (refunds live 100% in the economic-events lane) and no claim-by-default (correct
        # vendor registrations make the gate claim real AU vendors and exclude foreign ones).
        verdict = gst_service.classify(
            entity_registered=gst_service.entity_is_gst_registered(db, entity_id),
            account_applicable=gst_service.account_gst_applicable(db, contra_code, market),
            direction=direction,
            leg_touches_bank=True,
            gross=abs_amount,
            has_invoice=False,
            invoice_tax=None,
            vendor_registered_flag=vendor_flag,
        )
        if verdict.get("account") and verdict.get("amount", 0.0) > 0:
            return verdict["account"], float(verdict["amount"])
        return None, 0.0

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
        transaction.categorized_by_logic = 'manual'

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

            # Build entity COA context (active accounts, one entity at a time).
            # The master COA is GROUP-LEVEL (entity_id IS NULL) — only bank
            # accounts are entity-specific — so each entity's list is the shared
            # COA plus its own rows. (Was `entity_id == X AND status == "active"`,
            # which matched NOTHING: it dropped all 136 shared accounts and the
            # lowercase literal never equals AccountStatus.ACTIVE — the AI ran
            # with an EMPTY chart from the RAG wiring until 2026-07-26.)
            from sqlalchemy import or_
            from src.models.account import AccountStatus, FinanceAccount
            entity_ids = {ba.entity_id for ba in bank_accounts.values()}
            coa_by_entity: dict[int, list[dict]] = {}
            for entity_id in entity_ids:
                accounts = (
                    db.query(FinanceAccount)
                    .filter(
                        or_(
                            FinanceAccount.entity_id == entity_id,
                            FinanceAccount.entity_id.is_(None),
                        ),
                        FinanceAccount.status == AccountStatus.ACTIVE,
                    )
                    .order_by(FinanceAccount.code)
                    .all()
                )
                coa_by_entity[entity_id] = [
                    {"code": a.code, "name": a.name, "type": a.account_type.value
                     if hasattr(a.account_type, "value") else str(a.account_type)}
                    for a in accounts
                ]

            # RAG grounding: retrieved past categorizations + company facts.
            # Missing corpus/knowledge files degrade to the ungrounded prompt.
            from src.services.categorization_rag import (
                get_company_facts,
                get_default_retriever,
            )
            retriever = get_default_retriever()
            company_facts = get_company_facts()

            # Build transaction payloads. The COA is NOT repeated per transaction —
            # it goes once per entity in the prompt header (155 accounts × N txns
            # would blow the context at batch scale).
            txn_payloads = []
            for txn in transactions:
                ba = bank_accounts.get(txn.bank_account_id)
                entity_id = ba.entity_id if ba else None
                payload = {
                    "id": txn.id,
                    "description": txn.description or "",
                    "amount": float(txn.amount),
                    "currency": txn.currency,
                    "direction": "outgoing" if float(txn.amount) < 0 else "incoming",
                    "counterparty": txn.counterparty_name or "",
                    "bank_account": ba.account_name if ba else "",
                    "entity_id": entity_id,
                }
                if retriever is not None:
                    payload["similar_past"] = [
                        {
                            "description": e.description,
                            "account_code": e.account,
                            "account_name": e.account_name,
                            "score": round(score, 3),
                        }
                        for e, score in retriever.retrieve(txn.description or "", k=3)
                    ]
                txn_payloads.append(payload)

            facts_block = ""
            if company_facts:
                facts_block = (
                    "Company facts (verified ground truth about our business — "
                    "respect these when classifying):\n"
                    + "\n".join(f"- {f}" for f in company_facts)
                    + "\n\n"
                )

            client = anthropic.Anthropic(api_key=api_key)
            suggestion_map: dict[int, dict] = {}
            CHUNK = 40  # bounded response size per call; ~50 output tokens/txn
            for start in range(0, len(txn_payloads), CHUNK):
                chunk = txn_payloads[start:start + CHUNK]
                chunk_entities = {p["entity_id"] for p in chunk}
                coa_block = json_lib.dumps(
                    {str(eid): coa_by_entity.get(eid, []) for eid in chunk_entities})

                prompt = f"""You are a finance classification engine. Classify each bank transaction
to the most appropriate account in the chart of accounts.

{facts_block}Chart of accounts, keyed by entity_id (each transaction must use its own entity's list):
{coa_block}

Transactions to classify:
{json_lib.dumps(chunk, indent=2)}

For each transaction, return a JSON array (one object per transaction) with:
{{
  "id": <transaction id>,
  "account_code": "<code from this transaction's entity COA list>",
  "confidence": <0.00–1.00 — your confidence in this classification>,
  "reasoning": "<1 sentence plain-English explanation>"
}}

Rules:
- account_code MUST come from the COA list for that transaction's entity_id
- similar_past (when present) shows how WE categorized similar past transactions in
  our own audited history — weight this evidence strongly; only depart from it when
  the description clearly differs or a company fact contradicts it
- confidence >= 0.80 means you are confident; < 0.80 means uncertain
- For intercompany or payroll transactions that don't clearly fit any account, use confidence 0.50
- Return ONLY the JSON array, no other text"""

                # One bad chunk must not kill the whole AI phase — parse each
                # chunk independently and carry on.
                try:
                    message = client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=4096,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    raw = cast("TextBlock", message.content[0]).text.strip()
                    if raw.startswith("```"):
                        lines = raw.split("\n")
                        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                    suggestions = json_lib.loads(raw)
                    if not isinstance(suggestions, list):
                        raise ValueError("Expected JSON array from AI classification")
                except Exception as chunk_err:
                    logger.error(
                        f"AI classification chunk {start}-{start + len(chunk)} failed: "
                        f"{chunk_err}; raw head: {raw[:200] if 'raw' in locals() else '?'}",
                        exc_info=True)
                    continue
                for s in suggestions:
                    if "id" in s:
                        suggestion_map[s["id"]] = s

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

                    # BANK-CODE GUARD (2026-07-26): the AI may never book a
                    # simple entry against ANY bank account's COA code — money
                    # moving between our own accounts belongs to the transfer/IC
                    # lanes (rules + pairing), else it double-counts against the
                    # corridor JE. Route to review instead.
                    all_bank_codes = {
                        b.coa_account_code
                        for b in db.query(FinanceBankAccount).all()
                        if b.coa_account_code
                    }
                    if account_code in all_bank_codes:
                        txn.status = TransactionStatus.NEEDS_REVIEW
                        txn.categorized_by_logic = 'ai'
                        db.commit()
                        results[txn.id] = {
                            "transaction_id": txn.id,
                            "status": "needs_review",
                            "rule_name": f"[ai:bank-code-guard {account_code}]",
                            "journal_entry_id": None,
                            "error": None,
                        }
                        continue

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
                    txn.categorized_by_logic = 'ai'
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
                txn.categorized_by_logic = 'ai'
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

    # ' | '-separated patterns are alternatives (authoring convention carried over
    # from the QB-rule migration): CONTAINS/IS_EXACTLY match ANY alternative,
    # NOT_CONTAINS requires NONE present. Regex patterns are never split.
    alternatives = [p.strip() for p in p_lower.split("|") if p.strip()] or [p_lower]

    if operator == MatchOperator.CONTAINS:
        return any(alt in v_lower for alt in alternatives)
    if operator == MatchOperator.NOT_CONTAINS:
        return all(alt not in v_lower for alt in alternatives)
    if operator == MatchOperator.IS_EXACTLY:
        return any(v_lower == alt for alt in alternatives)
    if operator == MatchOperator.MATCHES_REGEX:
        try:
            return bool(re.search(pattern, value, re.IGNORECASE))
        except re.error:
            return p_lower in v_lower  # fallback: treat as substring
    return False


# Singleton instance
categorization_service = CategorizationService()
