"""A-3: RAG wiring into Phase 4D — corpus v2 loader, company facts, prompt injection."""
import json as json_lib
import os
from datetime import date
from unittest.mock import MagicMock, patch as _patch

import pytest
from sqlalchemy import Column, Integer as SAInteger, Table, create_engine
from sqlalchemy.orm import sessionmaker

from src.models.account import (
    AccountStatus, AccountType, FinanceAccount, NormalBalance,
)
from src.models.bank_account import BankAccountStatus, FinanceBankAccount
from src.database import Base
from src.models.entity import EntityStatus, FinanceEntity
from src.models.transaction import FinanceTransaction, TransactionStatus
from src.services.categorization_service import categorization_service
from src.services.categorization_rag import (
    build_corpus_from_v2_csv,
    get_company_facts,
    get_default_retriever,
    load_company_facts,
    reset_rag_cache,
)


@pytest.fixture(autouse=True)
def _clean_rag_cache():
    reset_rag_cache()
    yield
    reset_rag_cache()


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Table("users", Base.metadata, Column("id", SAInteger, primary_key=True),
          extend_existing=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def test_entity(db_session):
    entity = FinanceEntity(name="Test Company SG", country="SG",
                           base_currency="SGD", status=EntityStatus.ACTIVE)
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)
    return entity


@pytest.fixture
def test_accounts(db_session, test_entity):
    accounts = [
        FinanceAccount(code="1000", name="Cash at Bank", account_type=AccountType.ASSET,
                       normal_balance=NormalBalance.DEBIT, category="Assets",
                       status=AccountStatus.ACTIVE),
        FinanceAccount(code="5000", name="Office Expenses", account_type=AccountType.EXPENSE,
                       normal_balance=NormalBalance.DEBIT, category="Expenses",
                       status=AccountStatus.ACTIVE),
    ]
    db_session.add_all(accounts)
    db_session.commit()
    return {a.code: a for a in accounts}


@pytest.fixture
def test_bank_account(db_session, test_entity):
    ba = FinanceBankAccount(
        entity_id=test_entity.id, bank_name="OCBC", account_number="123-456-789",
        account_name="OCBC Current", currency="SGD",
        coa_account_code="1000", status=BankAccountStatus.ACTIVE,
    )
    db_session.add(ba)
    db_session.commit()
    db_session.refresh(ba)
    return ba


def _write_corpus(tmp_path, rows):
    p = tmp_path / "corpus_v2.csv"
    lines = ["entity,date,description,qb_label,coa_code,coa_name,amount,source"]
    lines += rows
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


def _write_knowledge(tmp_path, body):
    p = tmp_path / "KNOWLEDGE.md"
    p.write_text(body, encoding="utf-8")
    return str(p)


class TestCorpusV2Loader:
    def test_build_corpus_from_v2_csv(self, tmp_path):
        path = _write_corpus(tmp_path, [
            "sg_pte_ltd,19/01/2022,COLL CPF GIRO,HR - CPF,6001,Employer CPF (SG),-6366.0,quickbooks_gl_v2",
            "au,01/02/2023,LINKT TOLL SYD,Tolls,6401,Tolls & Parking,-12.5,quickbooks_gl_v2",
            "au,01/02/2023,,Tolls,6401,Tolls & Parking,-1.0,quickbooks_gl_v2",  # no desc → skipped
        ])
        corpus = build_corpus_from_v2_csv(path)
        assert len(corpus) == 2
        assert corpus[0].account == "6001"          # COA v2 CODE, not the QB label
        assert corpus[0].account_name == "Employer CPF (SG)"
        assert corpus[1].description == "LINKT TOLL SYD"
        assert corpus[1].amount == -12.5


class TestCompanyFactsLoader:
    BODY = """# Knowledge

## Entities
- **ENT-1** Only 3 real legal entities exist. *(Gaurav, 2026-06-01)*

## Counterparties
- **CP-7** Humax is a device subscription vendor. *(Gaurav, 2026-07-01)*

## Policies
- ~~**POL-2** Old superseded policy.~~ *(Gaurav, 2026-06-01)*
- **POL-4** Device subscriptions are expense; device purchases are capitalized. *(Gaurav, 2026-07-24)*

## Data quirks
- **DQ-9** Piped rule patterns are OR-alternatives. *(mining, 2026-07-24)*
"""

    def test_extracts_selected_sections_and_strips_provenance(self, tmp_path):
        path = _write_knowledge(tmp_path, self.BODY)
        facts = load_company_facts(path)
        ids = [f.split(":")[0] for f in facts]
        assert ids == ["ENT-1", "POL-4", "DQ-9"]
        assert all("Gaurav" not in f for f in facts)   # provenance tail stripped

    def test_skips_struck_and_cp_facts(self, tmp_path):
        path = _write_knowledge(tmp_path, self.BODY)
        facts = "\n".join(load_company_facts(path))
        assert "POL-2" not in facts     # superseded (struck)
        assert "CP-7" not in facts      # identity facts belong to the counterparty table

    def test_char_cap_truncates(self, tmp_path):
        path = _write_knowledge(tmp_path, self.BODY)
        facts = load_company_facts(path, max_chars=40)
        assert len(facts) == 1          # only ENT-1 fits


class TestDefaultRetrieverCache:
    def test_missing_corpus_returns_none(self, tmp_path):
        with _patch.dict(os.environ, {"RAG_CORPUS_PATH": str(tmp_path / "absent.csv")}):
            assert get_default_retriever() is None

    def test_missing_knowledge_returns_empty(self, tmp_path):
        with _patch.dict(os.environ, {"RAG_KNOWLEDGE_PATH": str(tmp_path / "absent.md")}):
            assert get_company_facts() == []

    def test_retriever_loads_and_caches(self, tmp_path):
        path = _write_corpus(tmp_path, [
            "au,01/02/2023,LINKT TOLL SYD,Tolls,6401,Tolls & Parking,-12.5,quickbooks_gl_v2",
        ])
        with _patch.dict(os.environ, {"RAG_CORPUS_PATH": path}):
            r1 = get_default_retriever()
            r2 = get_default_retriever()
        assert r1 is not None and r1 is r2


class TestPromptInjection:
    def _make_txn(self, db, bank_account, description, amount):
        import hashlib
        fp = hashlib.sha256(f"rag-{description}{amount}".encode()).hexdigest()
        txn = FinanceTransaction(
            bank_account_id=bank_account.id,
            transaction_date=date(2026, 3, 1),
            currency="SGD",
            description=description,
            amount=amount,
            fingerprint=fp,
            status=TransactionStatus.PENDING,
        )
        db.add(txn)
        db.commit()
        db.refresh(txn)
        return txn

    def _run_mocked(self, db, txn, env):
        mock_response = [{"id": txn.id, "account_code": "5000",
                          "confidence": 0.92, "reasoning": "test"}]
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=json_lib.dumps(mock_response))]
        with _patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", **env}):
            with _patch("anthropic.Anthropic") as MockAnthropic:
                MockAnthropic.return_value.messages.create.return_value = mock_msg
                result = categorization_service._run_ai_classification(db, [txn])
                call = MockAnthropic.return_value.messages.create.call_args
        prompt = call.kwargs["messages"][0]["content"]
        return result, prompt

    def test_prompt_includes_similar_past_and_facts(
        self, db_session, test_accounts, test_bank_account, tmp_path
    ):
        corpus = _write_corpus(tmp_path, [
            "sg_pte_ltd,19/01/2022,LINKT TOLL PAYMENT SYD,Tolls,6401,Tolls & Parking,-12.5,quickbooks_gl_v2",
        ])
        knowledge = _write_knowledge(tmp_path, TestCompanyFactsLoader.BODY)
        txn = self._make_txn(db_session, test_bank_account, "LINKT TOLL PAYMENT MEL", -15.0)

        result, prompt = self._run_mocked(
            db_session, txn,
            {"RAG_CORPUS_PATH": corpus, "RAG_KNOWLEDGE_PATH": knowledge},
        )

        assert txn.id in result
        assert "similar_past" in prompt
        assert "LINKT TOLL PAYMENT SYD" in prompt      # the retrieved example
        assert '"account_code": "6401"' in prompt
        assert "Company facts" in prompt
        assert "POL-4" in prompt                        # a company fact made it in
        assert "CP-7" not in prompt                     # CP facts excluded

    def test_graceful_without_corpus_or_knowledge(
        self, db_session, test_accounts, test_bank_account, tmp_path
    ):
        """No corpus, no knowledge → the pre-RAG blind prompt, still classifies."""
        txn = self._make_txn(db_session, test_bank_account, "SOME VENDOR PMT", -99.0)
        result, prompt = self._run_mocked(
            db_session, txn,
            {"RAG_CORPUS_PATH": str(tmp_path / "absent.csv"),
             "RAG_KNOWLEDGE_PATH": str(tmp_path / "absent.md")},
        )
        assert txn.id in result
        assert result[txn.id]["status"] == "categorized"
        assert '"similar_past"' not in prompt   # no retrieved-examples key in any payload
        assert "Company facts" not in prompt
