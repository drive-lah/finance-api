"""Tests for Option C: Team-based salary_expense_code derivation at onboarding.

Tests verify that salary_expense_code is NEVER NULL after onboarding:
- Derived from teams if not explicitly provided
- Customer Support → 5063
- On-Ground → 5061
- Else → 6000 (Salaries & Wages)

Both HrEmployee and FinanceCounterparty.default_account_code must be synced.
"""
import pytest
from sqlalchemy import create_engine, Table, Column, Integer as SAInteger, String as SAString, Boolean as SABoolean, Date as SADate
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models.entity import FinanceEntity, EntityStatus
from src.models.account import FinanceAccount, AccountType, NormalBalance, AccountStatus
from src.models.hr_employee import HrEmployee
from src.models.counterparty import FinanceCounterparty
from src.services.hr_onboarding_service import HrOnboardingService


@pytest.fixture
def db_engine():
    """Create in-memory SQLite engine."""
    engine = create_engine("sqlite:///:memory:")

    # Stub users table (from admin-bff)
    Table(
        "users", Base.metadata,
        Column("id", SAInteger, primary_key=True),
        Column("name", SAString(255), nullable=True),
        Column("email", SAString(255), nullable=True),
        Column("is_employee", SABoolean, nullable=True, default=False),
        Column("onboarding_status", SAString(20), nullable=True, default="PENDING"),
        Column("employee_type", SAString(20), nullable=True),
        Column("employment_end_date", SADate, nullable=True),
        Column("bank_account_number", SAString(50), nullable=True),
        Column("bank_code", SAString(20), nullable=True),
        Column("teams", SAString(500), nullable=True),
        extend_existing=True
    )

    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(db_engine):
    """Create session and setup base data."""
    Session = sessionmaker(bind=db_engine)
    session = Session()

    # Create test entity
    entity = FinanceEntity(
        id=2,
        name="Drive Lah Singapore",
        country="SG",
        base_currency="SGD",
        status=EntityStatus.ACTIVE,
    )
    session.add(entity)

    # Create test accounts
    accounts = {
        "5063": FinanceAccount(
            code="5063", name="Customer Support Salary",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT,
            category="Personnel", status=AccountStatus.ACTIVE
        ),
        "5061": FinanceAccount(
            code="5061", name="On-Ground Team Salary",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT,
            category="Personnel", status=AccountStatus.ACTIVE
        ),
        "5800": FinanceAccount(
            code="5800", name="Bonuses",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT,
            category="Personnel", status=AccountStatus.ACTIVE
        ),
        "6000": FinanceAccount(
            code="6000", name="Salaries & Wages",
            account_type=AccountType.EXPENSE,
            normal_balance=NormalBalance.DEBIT,
            category="Personnel", status=AccountStatus.ACTIVE
        ),
    }
    for acc in accounts.values():
        session.add(acc)

    # Create test users directly in the users table (stubbed)
    from sqlalchemy import text
    for user_id in range(101, 110):
        session.execute(
            text(
                "INSERT INTO users (id, name, email) "
                "VALUES (:id, :name, :email)"
            ),
            {
                "id": user_id,
                "name": f"User {user_id}",
                "email": f"user{user_id}@test.com",
            }
        )

    session.commit()
    return session


class TestOptionCSalaryDerivation:
    """Test Option C team-based salary derivation."""

    def test_onboard_without_salary_code_defaults_to_6000(self, db_session):
        """Test 1: Onboard without salary_expense_code → defaults to 6000."""
        service = HrOnboardingService()

        payload = {
            "user_id": 101,
            "user_name": "John Doe",
            "payroll_entity_id": 2,
            "employee_type": "FULL_TIME",
            "teams": [],
        }

        result = service.bulk_onboard(db_session, [payload])
        errors = result.get("errors", [])
        assert not errors, f"Onboarding failed: {errors}"

        # Verify HrEmployee has salary_expense_code = 6000
        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 101).first()
        assert emp is not None
        assert emp.salary_expense_code == "6000", f"Expected 6000, got {emp.salary_expense_code}"

        # Verify FinanceCounterparty has default_account_code = 6000
        cp = db_session.query(FinanceCounterparty).filter(
            FinanceCounterparty.external_id == "101",
            FinanceCounterparty.type == "employee"
        ).first()
        assert cp is not None
        assert cp.default_account_code == "6000", f"Expected 6000, got {cp.default_account_code}"

    def test_onboard_customer_support_derives_5063(self, db_session):
        """Test 2: Onboard with Customer Support team → derives 5063."""
        service = HrOnboardingService()

        payload = {
            "user_id": 102,
            "user_name": "Jane Smith",
            "payroll_entity_id": 2,
            "employee_type": "FULL_TIME",
            "teams": ["Customer Support"],
        }

        result = service.bulk_onboard(db_session, [payload])
        errors = result.get("errors", [])
        assert not errors, f"Onboarding failed: {errors}"

        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 102).first()
        assert emp is not None
        assert emp.salary_expense_code == "5063", f"Expected 5063, got {emp.salary_expense_code}"

        cp = db_session.query(FinanceCounterparty).filter(
            FinanceCounterparty.external_id == "102",
            FinanceCounterparty.type == "employee"
        ).first()
        assert cp is not None
        assert cp.default_account_code == "5063", f"Expected 5063, got {cp.default_account_code}"

    def test_onboard_on_ground_derives_5061(self, db_session):
        """Test 3: Onboard with On-Ground team → derives 5061."""
        service = HrOnboardingService()

        payload = {
            "user_id": 103,
            "user_name": "David Lee",
            "payroll_entity_id": 2,
            "employee_type": "FULL_TIME",
            "teams": ["On-Ground"],
        }

        result = service.bulk_onboard(db_session, [payload])
        errors = result.get("errors", [])
        assert not errors, f"Onboarding failed: {errors}"

        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 103).first()
        assert emp is not None
        assert emp.salary_expense_code == "5061", f"Expected 5061, got {emp.salary_expense_code}"

        cp = db_session.query(FinanceCounterparty).filter(
            FinanceCounterparty.external_id == "103",
            FinanceCounterparty.type == "employee"
        ).first()
        assert cp is not None
        assert cp.default_account_code == "5061", f"Expected 5061, got {cp.default_account_code}"

    def test_explicit_salary_code_overrides_team_derivation(self, db_session):
        """Test 4: Explicit salary_expense_code overrides team derivation."""
        service = HrOnboardingService()

        payload = {
            "user_id": 104,
            "user_name": "Alice Wong",
            "payroll_entity_id": 2,
            "employee_type": "FULL_TIME",
            "teams": ["Customer Support"],
            "salary_expense_code": "5800",  # Bonuses - explicit override
        }

        result = service.bulk_onboard(db_session, [payload])
        errors = result.get("errors", [])
        assert not errors, f"Onboarding failed: {errors}"

        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 104).first()
        assert emp is not None
        assert emp.salary_expense_code == "5800", f"Expected 5800 (override), got {emp.salary_expense_code}"

        cp = db_session.query(FinanceCounterparty).filter(
            FinanceCounterparty.external_id == "104",
            FinanceCounterparty.type == "employee"
        ).first()
        assert cp is not None
        assert cp.default_account_code == "5800", f"Expected 5800 (override), got {cp.default_account_code}"

    def test_multiple_teams_checks_first_match(self, db_session):
        """Test 5: Multiple teams - checks for Customer Support first."""
        service = HrOnboardingService()

        payload = {
            "user_id": 105,
            "user_name": "Bob Johnson",
            "payroll_entity_id": 2,
            "employee_type": "FULL_TIME",
            "teams": ["On-Ground", "Customer Support"],  # On-Ground first, but CS checked first in logic
        }

        result = service.bulk_onboard(db_session, [payload])
        errors = result.get("errors", [])
        assert not errors, f"Onboarding failed: {errors}"

        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 105).first()
        assert emp is not None
        # Logic checks CS first, so should match 5063
        assert emp.salary_expense_code == "5063", f"Expected 5063 (Customer Support), got {emp.salary_expense_code}"

    def test_never_null_salary_code_after_onboarding(self, db_session):
        """Test 6: salary_expense_code is NEVER NULL after onboarding."""
        service = HrOnboardingService()

        # Batch of employees with various team configs
        payloads = [
            {"user_id": 106, "user_name": "User 106", "payroll_entity_id": 2, "teams": [], "employee_type": "FULL_TIME"},
            {"user_id": 107, "user_name": "User 107", "payroll_entity_id": 2, "teams": ["Customer Support"], "employee_type": "PART_TIME"},
            {"user_id": 108, "user_name": "User 108", "payroll_entity_id": 2, "teams": ["On-Ground", "Engineering"], "employee_type": "FULL_TIME"},
        ]

        result = service.bulk_onboard(db_session, payloads)
        errors = result.get("errors", [])
        assert not errors, f"Onboarding failed: {errors}"

        # Check ALL employees have non-NULL salary_expense_code
        null_count = db_session.query(HrEmployee).filter(
            HrEmployee.salary_expense_code.is_(None),
            HrEmployee.user_id.in_([106, 107, 108])
        ).count()
        assert null_count == 0, f"Found {null_count} employees with NULL salary_expense_code"

        # Check ALL employee counterparties have non-NULL default_account_code
        null_count = db_session.query(FinanceCounterparty).filter(
            FinanceCounterparty.default_account_code.is_(None),
            FinanceCounterparty.type == "employee",
            FinanceCounterparty.external_id.in_(["106", "107", "108"])
        ).count()
        assert null_count == 0, f"Found {null_count} counterparties with NULL default_account_code"

    def test_both_tables_synced(self, db_session):
        """Test 7: HrEmployee and FinanceCounterparty are always in sync."""
        service = HrOnboardingService()

        payload = {
            "user_id": 109,
            "user_name": "Sync Test User",
            "payroll_entity_id": 2,
            "teams": ["Customer Support"],
            "employee_type": "FULL_TIME",
        }

        result = service.bulk_onboard(db_session, [payload])
        errors = result.get("errors", [])
        assert not errors, f"Onboarding failed: {errors}"

        emp = db_session.query(HrEmployee).filter(HrEmployee.user_id == 109).first()
        cp = db_session.query(FinanceCounterparty).filter(
            FinanceCounterparty.external_id == "109",
            FinanceCounterparty.type == "employee"
        ).first()

        assert emp.salary_expense_code == cp.default_account_code, \
            f"Mismatch: HrEmployee={emp.salary_expense_code}, FinanceCounterparty={cp.default_account_code}"
