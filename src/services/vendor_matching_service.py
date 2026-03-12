"""
Vendor Matching Service

Fuzzy-matches an AI-extracted vendor name against existing counterparties.
If no match is found, auto-creates a draft (unverified) counterparty.

Match algorithm (no external dependencies):
  1. Normalize: lowercase, strip punctuation and legal suffixes
  2. Exact match on normalized name → confidence 1.0
  3. Substring match (one contains the other) → confidence 0.85
  4. Token overlap ratio → confidence proportional to overlap

Thresholds:
  ≥ 0.80 → accepted match
  < 0.80 → no match → auto-create
"""
import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

from src.models.counterparty import FinanceCounterparty, CounterpartyType

logger = logging.getLogger(__name__)

# Legal suffixes to strip before comparing
_LEGAL_SUFFIXES = re.compile(
    r'\b(pte\.?\s*ltd\.?|pty\.?\s*ltd\.?|ltd\.?|llc\.?|inc\.?|corp\.?|'
    r'sdn\.?\s*bhd\.?|berhad|gmbh|co\.?|company|holdings?|group|'
    r'singapore|australia|sg|au)\b',
    re.IGNORECASE,
)
_PUNCT = re.compile(r'[^a-z0-9\s]')


def _normalize(name: str) -> str:
    name = name.lower()
    name = _LEGAL_SUFFIXES.sub('', name)
    name = _PUNCT.sub(' ', name)
    return ' '.join(name.split())  # collapse whitespace


def _token_overlap(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def fuzzy_match_vendor(
    vendor_name: str,
    counterparties: list[FinanceCounterparty],
    threshold: float = 0.80,
) -> tuple[Optional[FinanceCounterparty], float]:
    """
    Find the best matching counterparty for a vendor name.
    Returns (counterparty, confidence) or (None, 0.0).
    """
    norm_input = _normalize(vendor_name)
    if not norm_input:
        return None, 0.0

    best: Optional[FinanceCounterparty] = None
    best_score = 0.0

    for cp in counterparties:
        norm_cp = _normalize(cp.name)
        if not norm_cp:
            continue

        if norm_input == norm_cp:
            return cp, 1.0  # exact match — short-circuit

        score = 0.0
        if norm_input in norm_cp or norm_cp in norm_input:
            score = 0.85
        else:
            score = _token_overlap(norm_input, norm_cp)

        if score > best_score:
            best_score = score
            best = cp

    if best_score >= threshold:
        return best, best_score
    return None, best_score


class VendorMatchingService:
    """Matches extracted vendor names to counterparties; auto-creates if unmatched."""

    def match_or_create(
        self,
        db: Session,
        vendor_name: str,
        vendor_tax_id: Optional[str] = None,
    ) -> tuple[FinanceCounterparty, bool, float]:
        """
        Find or create a counterparty for the given vendor name.

        Returns:
          (counterparty, is_new, confidence)
          is_new=True means the counterparty was just auto-created (unverified)
        """
        if not vendor_name or not vendor_name.strip():
            return None, False, 0.0  # type: ignore[return-value]

        # Load all vendor-type counterparties
        candidates = (
            db.query(FinanceCounterparty)
            .filter(
                FinanceCounterparty.status == "active",
                FinanceCounterparty.type == CounterpartyType.VENDOR.value,
            )
            .all()
        )

        # Try tax ID match first (most reliable)
        if vendor_tax_id:
            for cp in candidates:
                if cp.tax_registration_number and cp.tax_registration_number == vendor_tax_id:
                    logger.info(f"Vendor matched by tax ID: {vendor_name} → {cp.name} (id={cp.id})")
                    return cp, False, 1.0

        # Fuzzy name match
        matched, confidence = fuzzy_match_vendor(vendor_name, candidates)
        if matched:
            logger.info(
                f"Vendor fuzzy matched: '{vendor_name}' → '{matched.name}' "
                f"(id={matched.id}, confidence={confidence:.2f})"
            )
            return matched, False, confidence

        # No match — auto-create unverified counterparty
        new_cp = FinanceCounterparty(
            name=vendor_name.strip(),
            type=CounterpartyType.VENDOR.value,
            tax_registration_number=vendor_tax_id,
            status="active",
            is_verified=False,
        )
        db.add(new_cp)
        db.flush()  # get ID without committing
        logger.info(f"Auto-created unverified counterparty: '{vendor_name}' (id={new_cp.id})")
        return new_cp, True, 0.0


vendor_matching_service = VendorMatchingService()
