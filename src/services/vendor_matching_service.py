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


def _cp_all_names(cp: FinanceCounterparty) -> list[str]:
    """Canonical name plus any recorded aliases (so an applied alias matches instantly next time)."""
    names = [cp.name] if cp.name else []
    aliases = getattr(cp, "aliases", None)
    if isinstance(aliases, list):
        names += [a for a in aliases if isinstance(a, str) and a.strip()]
    return names


def fuzzy_match_vendor(
    vendor_name: str,
    counterparties: list[FinanceCounterparty],
    threshold: float = 0.80,
) -> tuple[Optional[FinanceCounterparty], float]:
    """
    Find the best matching counterparty for a vendor name (checks name + aliases).
    Returns (counterparty, confidence) or (None, 0.0).
    """
    norm_input = _normalize(vendor_name)
    if not norm_input:
        return None, 0.0

    best: Optional[FinanceCounterparty] = None
    best_score = 0.0

    for cp in counterparties:
        for cand in _cp_all_names(cp):
            norm_cp = _normalize(cand)
            if not norm_cp:
                continue
            if norm_input == norm_cp:
                return cp, 1.0  # exact match (name or alias) — short-circuit
            score = 0.85 if (norm_input in norm_cp or norm_cp in norm_input) else _token_overlap(norm_input, norm_cp)
            if score > best_score:
                best_score = score
                best = cp

    if best_score >= threshold:
        return best, best_score
    return None, best_score


# ── LLM (Haiku) semantic matcher — catches abbreviations / legal-name variants
#    that pure string matching misses (URDrive→U R Drive, CDG→ComfortDelGro, etc.)
_anthropic_client = None


def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


def llm_match_vendor(
    vendor_name: str,
    counterparties: list[FinanceCounterparty],
    min_confidence: float = 0.7,
) -> tuple[Optional[FinanceCounterparty], float]:
    """
    Semantic match of an extracted vendor name against the counterparty list via Haiku.
    Returns (counterparty, confidence) or (None, 0.0). Fails safe to (None, 0.0) on any error.
    """
    import json
    if not vendor_name or not vendor_name.strip() or not counterparties:
        return None, 0.0
    by_id = {cp.id: cp for cp in counterparties}
    cp_lines = "\n".join(f"{cp.id}: {cp.name}" for cp in counterparties)
    prompt = (
        "You match an extracted invoice vendor name to our existing vendor counterparties.\n"
        "Handle abbreviations (URDrive=U R Drive, CDG=ComfortDelGro), legal-name variants "
        "(Income Insurance Limited=Income), extra descriptors, spacing, and word order.\n\n"
        f"Our counterparties (id: name):\n{cp_lines}\n\n"
        f'Extracted vendor name: "{vendor_name}"\n\n'
        'Return ONLY JSON: {"match_id": <int or null>, "confidence": <0-1>, "reason": "..."}. '
        "match_id null if it is genuinely a NEW vendor not in the list, or if it is our own "
        "company (Drive lah / Drive mate)."
    )
    try:
        msg = _get_anthropic().messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:-1] if text.rstrip().endswith("```") else text.split("\n")[1:])
        data = json.loads(text)
        mid, conf = data.get("match_id"), float(data.get("confidence") or 0)
        if mid and conf >= min_confidence and mid in by_id:
            logger.info(f"Vendor LLM-matched: '{vendor_name}' → '{by_id[mid].name}' (id={mid}, conf={conf:.2f})")
            return by_id[mid], conf
    except Exception as e:
        logger.warning(f"LLM vendor match failed for '{vendor_name}': {e}")
    return None, 0.0


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

        # Confident fuzzy match (name or alias) — cheap, no LLM
        matched, confidence = fuzzy_match_vendor(vendor_name, candidates, threshold=0.90)
        if matched:
            logger.info(
                f"Vendor fuzzy matched: '{vendor_name}' → '{matched.name}' "
                f"(id={matched.id}, confidence={confidence:.2f})"
            )
            return matched, False, confidence

        # Semantic fallback (Haiku) — catches abbreviations / legal-name variants
        matched, confidence = llm_match_vendor(vendor_name, candidates)
        if matched:
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
