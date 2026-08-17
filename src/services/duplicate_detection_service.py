"""
Duplicate Detection Engine (invoice ingestion)

A LAYERED, deterministic-first duplicate detector. The principle (Gaurav, 2026-08-01):
the vendor's INVOICE NUMBER is the decider; the AMOUNT corroborates. Two bills from
the same vendor with DIFFERENT invoice numbers are NOT duplicates even if the amount
and date match (vendors legitimately bill the same amount repeatedly). Detection is
certain when the invoice number exists, and HONEST (flag for review, never guess) when
it doesn't.

Layers (first hit wins):
  L1  hash        — byte-identical file (sha256 of pdf_content_hash)         -> BLOCK
  L2  semantic    — same entity + counterparty + invoice_number ...
                      ... + amount matches         -> BLOCK  (same bill)
                      ... + amount differs         -> REVIEW (revised/corrected invoice)
      (invoice number PRESENT but no match -> NOT a duplicate: different bill. Stop.)
  L3  fuzzy       — invoice number MISSING: same entity + counterparty + amount
                      + date + currency            -> REVIEW (can't confirm w/o a number)
  L4  llm (opt)   — advisory only, on the ambiguous tail; NEVER blocks       -> REVIEW

Money-path rule: only the deterministic layers (L1/L2-amount-match) may BLOCK promotion.
L3/L4 only raise a REVIEW flag — a human confirms. The LLM never gates the money path.

The engine is PURE: it reads candidate invoices and returns a verdict. It writes nothing.
"""
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models.invoice import FinanceInvoice, InvoiceStatus

logger = logging.getLogger(__name__)

# Statuses that make an invoice "occupy" an identity (a live bill). void/rejected don't.
# Everything that means "this invoice already exists" — ALL statuses except terminal REJECTED / VOID.
# needs_fix / reconcile / paired were previously (wrongly) excluded, which let a document already
# parked in needs_fix be re-uploaded (Gaurav 2026-08-09, Home & Away #03107).
_ACTIVE_STATUSES = [
    InvoiceStatus.DRAFT.value,
    InvoiceStatus.RECONCILE.value,
    InvoiceStatus.PAIRED.value,
    InvoiceStatus.NEEDS_FIX.value,
    InvoiceStatus.PENDING_APPROVAL.value,
    InvoiceStatus.APPROVED.value,
    InvoiceStatus.PARTIALLY_PAID.value,
    InvoiceStatus.PAID.value,
]

_AMOUNT_EPSILON = 0.01  # cents — amounts match when within this


@dataclass
class DuplicateVerdict:
    is_duplicate: bool                       # True only for a HARD (deterministic) duplicate
    action: str                              # 'block' | 'review' | 'none'
    level: Optional[str] = None              # 'hash'|'semantic'|'semantic_amount_mismatch'|'fuzzy'|'llm'|None
    duplicate_of: Optional[int] = None       # id of the matched original / best candidate
    candidates: list[int] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "is_duplicate": self.is_duplicate,
            "action": self.action,
            "level": self.level,
            "duplicate_of": self.duplicate_of,
            "candidates": self.candidates,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
        }


def _amounts_match(a, b) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) < _AMOUNT_EPSILON


class DuplicateDetectionService:
    """Layered, deterministic-first invoice duplicate detection (pure — no writes)."""

    def detect(
        self,
        db: Session,
        *,
        entity_id: Optional[int],
        counterparty_id: Optional[int],
        invoice_number: Optional[str],
        total_amount,
        invoice_date: Optional[date],
        currency: Optional[str],
        pdf_content_hash: Optional[str] = None,
        exclude_id: Optional[int] = None,
        use_llm: bool = False,
    ) -> DuplicateVerdict:
        """Return a DuplicateVerdict for a (prospective or existing) invoice.

        exclude_id: ignore this invoice id (use when re-checking an already-saved row).
        use_llm: enable the L4 advisory LLM pass on the ambiguous tail (never blocks).
        """
        base = db.query(FinanceInvoice).filter(
            FinanceInvoice.status.in_(_ACTIVE_STATUSES)
        )
        if exclude_id is not None:
            base = base.filter(FinanceInvoice.id != exclude_id)

        # ── L1: exact file hash ────────────────────────────────────────────────
        if pdf_content_hash:
            hit = base.filter(FinanceInvoice.pdf_content_hash == pdf_content_hash).order_by(
                FinanceInvoice.id.asc()
            ).first()
            if hit:
                return DuplicateVerdict(
                    is_duplicate=True, action="block", level="hash",
                    duplicate_of=hit.id, candidates=[hit.id], confidence=1.0,
                    reason=f"Byte-identical file already on invoice #{hit.id}.",
                )

        num = (invoice_number or "").strip()

        # ── L2: semantic (vendor + invoice number is the DECIDER) ───────────────
        if counterparty_id and num:
            # Entity scope is CONDITIONAL (Gaurav live-test 2026-08-17): the extract stage often
            # has no entity resolved yet, and a same-vendor+same-number hit in ANY entity is a
            # duplicate signal — an unscoped check must not silently pass.
            q = base.filter(
                FinanceInvoice.counterparty_id == counterparty_id,
                func.lower(func.trim(FinanceInvoice.invoice_number)) == num.lower(),
            )
            if entity_id is not None:
                q = q.filter(FinanceInvoice.entity_id == entity_id)
            rows = q.order_by(FinanceInvoice.id.asc()).all()
            if rows:
                exact = next((r for r in rows if _amounts_match(r.total_amount, total_amount)), None)
                if exact:
                    return DuplicateVerdict(
                        is_duplicate=True, action="block", level="semantic",
                        duplicate_of=exact.id, candidates=[r.id for r in rows], confidence=1.0,
                        reason=f"Same vendor + invoice number + amount as invoice #{exact.id}.",
                    )
                return DuplicateVerdict(
                    is_duplicate=False, action="review", level="semantic_amount_mismatch",
                    duplicate_of=rows[0].id, candidates=[r.id for r in rows], confidence=0.6,
                    reason=(f"Same vendor + invoice number as invoice #{rows[0].id} but a "
                            f"DIFFERENT amount — likely a revised/corrected invoice. Review."),
                )
            # invoice number present and unmatched → a genuinely different bill. NOT a dup.
            return DuplicateVerdict(
                is_duplicate=False, action="none", level=None,
                reason="Invoice number not seen before for this vendor — a distinct bill.",
            )

        # ── L3: fuzzy fallback — ONLY when the invoice number is missing ────────
        # (never fuzzy-match on amount when a number is present: that is the
        #  recurring-same-amount safeguard.)
        if counterparty_id and total_amount is not None and invoice_date is not None:
            q = base.filter(
                FinanceInvoice.counterparty_id == counterparty_id,
                FinanceInvoice.invoice_date == invoice_date,
                FinanceInvoice.currency == currency,
            )
            if entity_id is not None:
                q = q.filter(FinanceInvoice.entity_id == entity_id)
            rows = q.all()
            same_amt = [r for r in rows if _amounts_match(r.total_amount, total_amount)]
            if same_amt:
                same_amt.sort(key=lambda r: r.id)
                verdict = DuplicateVerdict(
                    is_duplicate=False, action="review", level="fuzzy",
                    duplicate_of=same_amt[0].id, candidates=[r.id for r in same_amt], confidence=0.5,
                    reason=(f"Same vendor + amount + date as invoice #{same_amt[0].id}, but NO "
                            f"invoice number to confirm — needs a human check."),
                )
                # ── L4: optional LLM advisory on the ambiguous tail (never blocks) ──
                if use_llm:
                    verdict = self._llm_refine(verdict, invoice_number, total_amount, same_amt)
                return verdict

        return DuplicateVerdict(is_duplicate=False, action="none", level=None,
                                reason="No duplicate signal.")

    def _llm_refine(self, verdict: DuplicateVerdict, invoice_number, total_amount, candidates) -> DuplicateVerdict:
        """L4 advisory: let a cheap LLM judge the ambiguous same-amount candidates.

        Advisory ONLY — it may raise/adjust confidence and pick the best candidate, but
        it NEVER flips action to 'block'. Fails safe to the deterministic verdict.
        """
        try:
            import anthropic  # noqa: F401
            # Kept intentionally light: the deterministic verdict already says 'review'
            # with candidates. A full LLM comparison would fetch each candidate's text;
            # that runs at bulk-recheck time, not on the hot upload path. For now we only
            # annotate that an LLM pass is warranted, preserving the deterministic result.
            verdict.reason += " (LLM advisory pass available for tie-break.)"
        except Exception as e:  # pragma: no cover
            logger.warning(f"LLM refine unavailable: {e}")
        return verdict


duplicate_detection_service = DuplicateDetectionService()
