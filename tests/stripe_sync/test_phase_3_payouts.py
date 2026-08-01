"""Phase 3 Integration Test: Host Payouts by PayoutType

Tests JEs #8-15 using payout_entries and payoutType mapping.
"""
import pytest
from datetime import date
from decimal import Decimal

from src.services.stripe_sync.config import PAYOUTTYPE_TO_ACCOUNT, REGIONS
from src.services.stripe_sync.query_builder import QueryBuilder
from src.services.stripe_sync.journal_entry_builder import JournalEntryBuilder


class TestPhase3PayoutMapping:
    """Test payoutType → COA mapping."""

    def test_payouttype_to_account_mapping_complete(self):
        """Verify all required payoutTypes have account mappings."""
        required_types = [
            "damage",
            "excess_mileage",
            "flexplus",
            "fuel_refund",
            "fuel_charge",
            "misc_payout",
            "cleanliness",
            "tolls",
            "late_return",
        ]
        
        for payout_type in required_types:
            assert payout_type in PAYOUTTYPE_TO_ACCOUNT, f"Missing mapping for {payout_type}"
            account_code = PAYOUTTYPE_TO_ACCOUNT[payout_type]
            assert account_code.startswith("5") or account_code.startswith("4"), \
                f"Invalid account code {account_code} for {payout_type}"

    def test_payouttype_account_codes_match_coa(self):
        """Verify account codes are valid GL codes."""
        valid_prefixes = ["5", "4", "2"]  # Expense, Revenue, Liability
        
        for payout_type, account_code in PAYOUTTYPE_TO_ACCOUNT.items():
            # Account code should be 4 digits starting with valid prefix
            assert len(account_code) == 4, f"Invalid code length: {account_code}"
            assert account_code[0] in valid_prefixes, \
                f"Invalid account prefix for {payout_type}: {account_code}"

    def test_fuel_charge_vs_fuel_refund_mapping(self):
        """Fuel charge and refund both map to 5023."""
        assert PAYOUTTYPE_TO_ACCOUNT["fuel_charge"] == "5023"
        assert PAYOUTTYPE_TO_ACCOUNT["fuel_refund"] == "5023"


class TestPhase3QueryBuilder:
    """Test Phase 3 query builder methods."""

    @pytest.fixture
    def qb_sg(self):
        return QueryBuilder("SG")

    @pytest.fixture
    def qb_au(self):
        return QueryBuilder("AU")

    def test_host_payout_by_payouttype_query_format(self, qb_sg):
        """Verify query builder generates valid ClickHouse query."""
        query = qb_sg.host_payout_by_payouttype("2025-01", "damage", "positive")
        
        # Should contain key components
        assert "sg_payout_entries" in query
        assert "payoutType = 'damage'" in query
        assert "createdAt" in query
        assert "payoutAmount > 0" in query
        assert "finance_status IS NULL" in query or "Under Review" in query

    def test_host_payout_by_payouttype_positive_filter(self, qb_sg):
        """Verify positive amount filter is applied."""
        query_pos = qb_sg.host_payout_by_payouttype("2025-01", "damage", "positive")
        assert "payoutAmount > 0" in query_pos

    def test_host_payout_by_payouttype_negative_filter(self, qb_sg):
        """Verify negative amount filter is applied."""
        query_neg = qb_sg.host_payout_by_payouttype("2025-01", "fuel_charge", "negative")
        assert "payoutAmount < 0" in query_neg

    def test_host_payout_by_payouttype_any_filter(self, qb_sg):
        """Verify 'any' filter omits amount comparison."""
        query_any = qb_sg.host_payout_by_payouttype("2025-01", "damage", "any")
        assert "payoutAmount >" not in query_any
        assert "payoutAmount <" not in query_any

    def test_table_name_sg(self, qb_sg):
        """SG queries should use sg_payout_entries."""
        query = qb_sg.host_payout_by_payouttype("2025-01", "damage")
        assert "sg_payout_entries" in query

    def test_table_name_au(self, qb_au):
        """AU queries should use au_payout_entries."""
        query = qb_au.host_payout_by_payouttype("2025-01", "damage")
        assert "au_payout_entries" in query

    def test_company_account_detection_query(self, qb_sg):
        """Verify company account detection query is valid."""
        query = qb_sg.get_company_owned_accounts()
        assert "stripe_connected_accounts" in query
        assert "linked_account" in query


class TestPhase3JournalEntryBuilder:
    """Test Phase 3 JE builder."""

    @pytest.fixture
    def builder_sg(self):
        return JournalEntryBuilder("SG")

    def test_build_payout_je_damage(self, builder_sg):
        """Test building a damage payout JE."""
        entry_date = date(2025, 1, 1)
        amount = Decimal("1000.00")
        
        je_args = builder_sg.build_payout_je("damage", amount, entry_date, entity_id=2)
        
        assert je_args.entity_id == 2
        assert je_args.entry_date == entry_date
        assert "damage" in je_args.description.lower()
        assert len(je_args.lines) == 2
        
        # Verify debit and credit
        debit_line = next(l for l in je_args.lines if l["is_debit"])
        credit_line = next(l for l in je_args.lines if not l["is_debit"])
        
        assert debit_line["account_code"] == "5021"  # Damage payout
        assert credit_line["account_code"] == "2120"  # Host Payables
        assert debit_line["amount"] == amount
        assert credit_line["amount"] == amount

    def test_build_payout_je_reference_format(self, builder_sg):
        """Test reference number format: STRIPE-{REGION}-{SUFFIX}-{YYYY-MM}."""
        entry_date = date(2025, 1, 1)
        je_args = builder_sg.build_payout_je("damage", Decimal("100"), entry_date, entity_id=2)
        
        # Format: STRIPE-SG-A-HOST-DAMAGE-2025-01
        assert je_args.reference_number.startswith("STRIPE-SG-")
        assert "-2025-01" in je_args.reference_number
        assert "DAMAGE" in je_args.reference_number.upper()

    def test_build_payout_je_invalid_type(self, builder_sg):
        """Test error handling for invalid payoutType."""
        entry_date = date(2025, 1, 1)
        
        with pytest.raises(ValueError, match="Unknown payoutType"):
            builder_sg.build_payout_je("invalid_type", Decimal("100"), entry_date, entity_id=2)

    def test_build_payout_je_all_types(self, builder_sg):
        """Test building JEs for all mapped payoutTypes."""
        entry_date = date(2025, 1, 1)
        amount = Decimal("100.00")
        
        payout_types = list(PAYOUTTYPE_TO_ACCOUNT.keys())[:9]  # First 9 types
        
        for payout_type in payout_types:
            je_args = builder_sg.build_payout_je(payout_type, amount, entry_date, entity_id=2)
            
            assert je_args.entity_id == 2
            assert len(je_args.lines) == 2
            assert je_args.lines[0]["is_debit"] == True
            assert je_args.lines[1]["is_debit"] == False
            
            # Verify debit account matches mapping
            debit_account = je_args.lines[0]["account_code"]
            assert debit_account == PAYOUTTYPE_TO_ACCOUNT[payout_type]


class TestPhase3Regions:
    """Test regional configurations."""

    def test_sg_region_config(self):
        """Verify SG region configuration."""
        sg_config = REGIONS["SG"]
        
        assert sg_config["entity_id"] == 2
        assert sg_config["currency"] == "SGD"
        assert sg_config["stripe_platform_account"] == "1017"
        assert sg_config["stripe_connect_account"] == "1018"
        assert sg_config["company_bank"] == "OCBC"

    def test_au_region_config(self):
        """Verify AU region configuration."""
        au_config = REGIONS["AU"]
        
        assert au_config["entity_id"] == 3
        assert au_config["currency"] == "AUD"
        assert au_config["stripe_platform_account"] == "1019"
        assert au_config["stripe_connect_account"] == "1020"
        assert au_config["company_bank"] == "CMB"
