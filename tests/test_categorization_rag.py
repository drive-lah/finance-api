"""Tests for the categorization RAG core (embedder, GL corpus, retriever)."""
import math

from src.services.categorization_rag import (
    HashingEmbedder, CorpusEntry, CategorizationRetriever, build_corpus_from_gl_csv,
)


def test_hashing_embedder_deterministic_and_normalized():
    emb = HashingEmbedder(dim=256)
    a1 = emb.embed(["UBER TRIP SYDNEY"])[0]
    a2 = emb.embed(["UBER TRIP SYDNEY"])[0]
    assert a1 == a2  # deterministic
    assert abs(math.sqrt(sum(x * x for x in a1)) - 1.0) < 1e-9  # L2-normalised
    # shared tokens → higher cosine than unrelated text
    uber2 = emb.embed(["UBER TRIP MELBOURNE"])[0]
    other = emb.embed(["STRIPE PROCESSING FEE"])[0]
    cos = lambda u, v: sum(x * y for x, y in zip(u, v))
    assert cos(a1, uber2) > cos(a1, other)


def test_corpus_build_from_gl_csv(tmp_path):
    """Only bank-section rows become labelled (description → category); the mirror
    row under the expense section is excluded; the aggregate JE is excluded."""
    csv_text = (
        "Co,,,,,,,,,\n"
        "General Ledger,,,,,,,,,\n"
        '"Jan 2026",,,,,,,,,\n'
        ",,,,,,,,,\n"
        ",Distribution account,Transaction date,Transaction type,No.,Name,Description,Split,Amount,Balance\n"
        "1101 Savings,,,,,,,,,\n"
        ",1101 Savings,16/01/2026,Expense,,,UBER TRIP SYDNEY,Operating Expenses:Travel,\"-25.00\",\"100.00\"\n"
        ",1101 Savings,17/01/2026,Expense,,,STRIPE FEE,Operating Expenses:Bank Fees,\"-3.00\",\"97.00\"\n"
        ",1101 Savings,31/01/2026,Journal Entry,JE1,,Jan aggregate revenue,4000 Revenue,\"5000.00\",\"5097.00\"\n"
        "5000 Expenses,,,,,,,,,\n"
        ",5000 Expenses,16/01/2026,Expense,,,UBER TRIP SYDNEY,1101 Savings,\"25.00\",\"25.00\"\n"
    )
    p = tmp_path / "gl.csv"
    p.write_text(csv_text, encoding="utf-8")
    corpus = build_corpus_from_gl_csv(str(p))

    # 2 bank-section bank-line rows; JE excluded (not a bank-line type); expense-section row excluded
    assert len(corpus) == 2
    by_desc = {e.description: e for e in corpus}
    assert by_desc["UBER TRIP SYDNEY"].account == "Operating Expenses:Travel"
    assert by_desc["STRIPE FEE"].account == "Operating Expenses:Bank Fees"
    assert by_desc["UBER TRIP SYDNEY"].amount == -25.0


def test_retriever_suggests_account_from_similar_history():
    corpus = [
        CorpusEntry("Direct Credit 421520 CASSIE CROWDER", "4301 Incidental Revenue"),
        CorpusEntry("Direct Credit 509196 INSURET CLAIM 1225", "4301 Incidental Revenue"),
        CorpusEntry("UBER TRIP SYDNEY", "Operating Expenses:Travel"),
        CorpusEntry("UBER TRIP BRISBANE", "Operating Expenses:Travel"),
        CorpusEntry("STRIPE PROCESSING FEE", "Operating Expenses:Bank Fees"),
    ]
    r = CategorizationRetriever(corpus)

    # A new travel line → travel (shares "uber trip")
    s = r.suggest_account("UBER TRIP MELBOURNE", k=3)
    assert s["account"] == "Operating Expenses:Travel"

    # A new incidental-revenue line → incidental (shares "cassie crowder")
    s2 = r.suggest_account("Fast Transfer From CASSIE CROWDER reference", k=3)
    assert s2["account"] == "4301 Incidental Revenue"
    assert s2["examples"][0]["account"] == "4301 Incidental Revenue"  # nearest neighbour


def test_retriever_empty_corpus_returns_none():
    r = CategorizationRetriever([])
    assert r.retrieve("anything") == []
    assert r.suggest_account("anything") is None


def test_retriever_accepts_pluggable_fake_embedder():
    """A deterministic fake embedder drives retrieval — proves provider-independence."""
    class FakeEmbedder:
        dim = 2
        # map by keyword to fixed unit vectors
        def embed(self, texts):
            out = []
            for t in texts:
                out.append([1.0, 0.0] if "travel" in t.lower() else [0.0, 1.0])
            return out

    corpus = [
        CorpusEntry("travel one", "Travel"),
        CorpusEntry("bank fee one", "Bank Fees"),
    ]
    r = CategorizationRetriever(corpus, embedder=FakeEmbedder())
    assert r.suggest_account("some travel thing", k=1)["account"] == "Travel"
    assert r.suggest_account("some fee thing", k=1)["account"] == "Bank Fees"
