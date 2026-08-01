"""
Categorization RAG — retrieve how similar PAST transactions were categorized.

The cascade's Route 7 (AI fallback) must not run blind. This module supplies the
most similar past *confirmed* categorizations (from QuickBooks GL history) for a
transaction description, so the model generalises from our own history instead of
guessing. Rules stay the deterministic backbone; this handles the long tail.

Pluggable by design:
- `Embedder` (Protocol): text → vector. The default `HashingEmbedder` is
  deterministic + dependency-free (real cosine retrieval, no torch/API), so the
  retriever is fully testable offline. A neural provider (sentence-transformers /
  fastembed / Voyage / OpenAI) can implement the same `.embed()` for production
  without changing the retriever.
- `CorpusEntry`: one labelled (description → account) example.
- `build_corpus_from_gl_csv`: parse a QuickBooks General Ledger export → entries.
- `CategorizationRetriever`: brute-force cosine top-k (no vector DB at this scale)
  + a majority-vote account suggestion.
"""
from __future__ import annotations

import csv
import hashlib
import math
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Deterministic, dependency-free bag-of-tokens hashing embedder (L2-normalised).

    Real cosine retrieval (shared tokens → high similarity) with no model download
    or API. The production swap is just another class implementing `embed()`.
    """

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _tokens(text):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            v[h % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v))
        if norm:
            v = [x / norm for x in v]
        return v

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


@dataclass(frozen=True)
class CorpusEntry:
    description: str
    account: str          # the category — COA code (v2 corpus) or GL 'Split' label (v1)
    amount: float = 0.0
    txn_type: str = ""
    source: str = "quickbooks_gl"
    account_name: str = ""


# GL transaction types that represent real bank lines (vs aggregate journal entries)
_BANK_LINE_TYPES = {"Expense", "Cheque", "Deposit", "Bill Payment (Cheque)", "Transfer"}
# A section is a bank/cash account → its rows' `Split` is the meaningful category.
_BANK_SECTION = re.compile(r"^(?:11\d|112233|stripe|saving|wise|bank|cash|clearing|petty)", re.I)


def build_corpus_from_gl_csv(path: str) -> list[CorpusEntry]:
    """Parse a QuickBooks General Ledger CSV → labelled (description → account) entries.

    Keeps rows whose *distribution account* (the section) is a bank/cash account, so
    the `Split` column is the meaningful category (expense/revenue/AP/transfer target),
    not the bank side. Dedupes the GL's two-sided representation.

    GL columns: [0]blank [1]Distribution acct [2]date [3]type [4]No [5]Name
                [6]Description [7]Split [8]Amount [9]Balance
    """
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    hdr = next((i for i, r in enumerate(rows) if len(r) > 2 and r[2].strip() == "Transaction date"), 4)
    out: list[CorpusEntry] = []
    seen: set[tuple] = set()
    for r in rows[hdr + 1:]:
        if len(r) < 9:
            continue
        section, date, ttype = r[1].strip(), r[2].strip(), r[3].strip()
        desc, split, amount = r[6].strip(), r[7].strip(), r[8].strip()
        if not (date and ttype and desc and split):
            continue
        if ttype not in _BANK_LINE_TYPES:
            continue
        if not _BANK_SECTION.match(section):
            continue
        try:
            amt = float(amount.replace(",", "")) if amount else 0.0
        except ValueError:
            amt = 0.0
        key = (date, desc, amount, split)
        if key in seen:
            continue
        seen.add(key)
        out.append(CorpusEntry(description=desc, account=split, amount=amt, txn_type=ttype))
    return out


def build_corpus_from_v2_csv(path: str) -> list[CorpusEntry]:
    """Load corpus v2 (documentation/wip/reconciliation/corpus_v2/corpus_v2.csv).

    v2 is the cleaned, bridge-relabelled corpus: party/rule-covered lines removed,
    labels are COA v2 CODES — the exact output vocabulary the classifier must use.
    Columns: entity,date,description,qb_label,coa_code,coa_name,amount,source
    """
    out: list[CorpusEntry] = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            desc, code = (r.get("description") or "").strip(), (r.get("coa_code") or "").strip()
            if not desc or not code:
                continue
            try:
                amt = float(r.get("amount") or 0.0)
            except ValueError:
                amt = 0.0
            out.append(CorpusEntry(
                description=desc, account=code, amount=amt,
                source=r.get("source") or "quickbooks_gl_v2",
                account_name=(r.get("coa_name") or "").strip(),
            ))
    return out


_FACT_LINE = re.compile(r"^- \*\*(?P<id>[A-Z]+-\d+)\*\*\s*(?P<text>.+)$")
_PROVENANCE_TAIL = re.compile(r"\s*\*\([^)]*\)\*\s*$")


def load_company_facts(
    path: str,
    sections: tuple[str, ...] = ("ENT", "FLOW", "POL", "DQ"),
    max_chars: int = 6000,
) -> list[str]:
    """Extract numbered business facts from documentation/KNOWLEDGE.md.

    Defaults to ENT/FLOW/POL/DQ — CP identity facts are the counterparty table's
    job (POL-12), not the prompt's. Struck-through (superseded) facts are skipped;
    the `*(source, date)*` provenance tail is stripped to save tokens.
    """
    facts: list[str] = []
    used = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if "~~" in line:
                continue
            m = _FACT_LINE.match(line)
            if not m:
                continue
            fact_id = m.group("id")
            if fact_id.split("-")[0] not in sections:
                continue
            text = _PROVENANCE_TAIL.sub("", m.group("text")).strip()
            entry = f"{fact_id}: {text}"
            if used + len(entry) > max_chars:
                break
            facts.append(entry)
            used += len(entry)
    return facts


# ---------------------------------------------------------------------------
# Default (lazy, module-cached) retriever + facts for the live engine.
# Paths overridable via env; missing files degrade to None/[] so the AI
# fallback simply runs without grounding (the pre-RAG behaviour).
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_CORPUS_PATH = os.path.join(
    _REPO_ROOT, "documentation", "wip", "reconciliation", "corpus_v2", "corpus_v2.csv")
DEFAULT_KNOWLEDGE_PATH = os.path.join(_REPO_ROOT, "documentation", "KNOWLEDGE.md")

_rag_lock = threading.Lock()
_default_retriever: Optional["CategorizationRetriever"] = None
_default_retriever_loaded = False
_company_facts: Optional[list[str]] = None


def reset_rag_cache() -> None:
    """Test helper: force the next getter call to re-read paths from disk/env."""
    global _default_retriever, _default_retriever_loaded, _company_facts
    with _rag_lock:
        _default_retriever = None
        _default_retriever_loaded = False
        _company_facts = None


def get_default_retriever() -> Optional["CategorizationRetriever"]:
    global _default_retriever, _default_retriever_loaded
    if not _default_retriever_loaded:
        with _rag_lock:
            if not _default_retriever_loaded:
                path = os.environ.get("RAG_CORPUS_PATH", DEFAULT_CORPUS_PATH)
                if os.path.exists(path):
                    corpus = build_corpus_from_v2_csv(path)
                    if corpus:
                        _default_retriever = CategorizationRetriever(corpus)
                _default_retriever_loaded = True
    return _default_retriever


def get_company_facts() -> list[str]:
    global _company_facts
    if _company_facts is None:
        with _rag_lock:
            if _company_facts is None:
                path = os.environ.get("RAG_KNOWLEDGE_PATH", DEFAULT_KNOWLEDGE_PATH)
                _company_facts = load_company_facts(path) if os.path.exists(path) else []
    return list(_company_facts)


def _cosine(a: list[float], b: list[float]) -> float:
    # Both vectors are L2-normalised, so dot product == cosine similarity.
    return sum(x * y for x, y in zip(a, b))


class CategorizationRetriever:
    """Brute-force cosine retrieval over a labelled corpus + majority-vote suggestion."""

    def __init__(self, corpus: list[CorpusEntry], embedder: Optional[Embedder] = None) -> None:
        self.embedder: Embedder = embedder or HashingEmbedder()
        self.corpus = corpus
        self._vecs = self.embedder.embed([e.description for e in corpus]) if corpus else []

    def retrieve(self, description: str, k: int = 5) -> list[tuple[CorpusEntry, float]]:
        if not self.corpus:
            return []
        q = self.embedder.embed([description])[0]
        scored = sorted(
            (
                (entry, _cosine(q, vec))
                for entry, vec in zip(self.corpus, self._vecs)
            ),
            key=lambda t: t[1],
            reverse=True,
        )
        return scored[:k]

    def suggest_account(self, description: str, k: int = 5, min_score: float = 0.0) -> Optional[dict]:
        """Top-k retrieval → similarity-weighted majority-vote account + the evidence."""
        hits = [(e, s) for e, s in self.retrieve(description, k) if s > min_score]
        if not hits:
            return None
        total = sum(s for _, s in hits) or 1.0
        votes = Counter(e.account for e, _ in hits)
        account, n = votes.most_common(1)[0]
        confidence = sum(s for e, s in hits if e.account == account) / total
        return {
            "account": account,
            "confidence": round(confidence, 3),
            "votes": f"{n}/{len(hits)}",
            "examples": [
                {"description": e.description, "account": e.account, "score": round(s, 3)}
                for e, s in hits
            ],
        }
